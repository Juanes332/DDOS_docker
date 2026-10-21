"""Flood HTTP por ráfagas contra el destino del laboratorio.

Modo de uso principal:

    --requests N --every S    lanza N peticiones y espera S segundos
    --forever                 repite hasta Ctrl+C

El nombre DNS se resuelve una sola vez; las peticiones van a la IP ya validada
y llevan el `Host` original para que el servidor virtual correcto responda.
"""

import argparse
import asyncio
import json
import signal
import ssl
import time

from guard import (
    MAX_CONCURRENCY,
    MAX_DURATION,
    MAX_EVERY,
    MAX_REQUESTS,
    GuardError,
    clamp,
    parse_target,
    resolve,
)


async def _one(
    ip: str,
    port: int,
    path: str,
    host_header: str,
    sni: str,
    ctx,
    timeout: float,
    sem,
) -> tuple[int, float]:
    """Una peticion GET. Devuelve (codigo, latencia_ms); 0 si no hubo respuesta."""
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
            writer.write(
                (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {host_header}\r\n"
                    "User-Agent: ddos-lab/2.0\r\n"
                    "Accept: */*\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
            )
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
    ip, port, path, host_header, sni, ctx, requests, timeout, sem
) -> list[tuple[int, float]]:
    """Lanza `requests` peticiones en paralelo, acotadas por el semaforo."""
    tasks = [
        asyncio.create_task(
            _one(ip, port, path, host_header, sni, ctx, timeout, sem)
        )
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
        print(f"[guard] ritmo ajustado a {requests} peticiones cada {every:g}s")

    print("=" * 66)
    scheme = "https" if args.https else "http"
    print(f"  DESTINO   : {scheme}://{args.dns}:{args.port}{args.path}  ->  {ip}")
    print(f"  PATRON    : {requests} peticiones cada {every:g}s"
          f"  ({requests / every:.0f} req/s por contenedor)")
    print(f"  MODO      : {'INFINITO (Ctrl+C para parar)' if args.forever else f'{args.duration:g}s'}")
    print(f"  AMBITO    : solo red local; sin salida a Internet")
    print("=" * 66, flush=True)

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
            args.path,
            host_header,
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
        description="Flood HTTP por rafagas (laboratorio de red local)"
    )
    parser.add_argument("--dns", required=True, help="nombre DNS o IP del destino")
    parser.add_argument("--port", type=int, required=True, help="puerto del destino")
    parser.add_argument("--path", default="/", help="ruta HTTP (def. /)")
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

    try:
        host, port = parse_target(args.dns, args.port)
        ip = resolve(host, port)
    except GuardError as exc:
        print(f"[abortado] {exc}")
        raise SystemExit(2)

    args.dns, args.port = host, port
    if port == 443:
        args.https = True
    host_header = host if port in (80, 443, 8080) else f"{host}:{port}"

    if not args.forever:
        args.duration = clamp("duration", args.duration, MAX_DURATION)

    try:
        asyncio.run(run(args, str(ip), host_header))
    except KeyboardInterrupt:
        print("\n  interrumpido")


if __name__ == "__main__":
    main()
