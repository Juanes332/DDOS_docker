"""Resolución y validación del destino de ataque.

El destino se escribe como nombre DNS + puerto. Aquí se resuelve una sola vez
(para no pagar un lookup por petición) y se comprueba que la dirección sea de
la red local: loopback, RFC1918 o link-local.

La comprobación es deliberada y simple: este laboratorio no genera tráfico hacia
direcciones públicas. Si el nombre resuelve a Internet, se aborta antes de abrir
un solo socket.
"""

import ipaddress
import socket

# Topes duros del laboratorio.
MAX_REQUESTS = 500       # peticiones por ráfaga
MAX_CONCURRENCY = 400    # peticiones simultáneas
MAX_SOCKETS = 1000       # slowloris
MAX_CONNECTIONS = 500    # cola TCP
MAX_EVERY = 3600.0       # segundos entre ráfagas
MAX_DURATION = 86400     # segundos (24 h) cuando no es infinito


class GuardError(RuntimeError):
    """El destino o los parámetros no cumplen las reglas del laboratorio."""


def resolve(host: str, port: int) -> ipaddress.IPv4Address:
    """Resuelve `host` y exige que apunte a la red local."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise GuardError(
            f"no se pudo resolver '{host}': {exc}\n"
            "  comprueba el nombre o usa la IP directamente"
        ) from exc

    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue

    if not addresses:
        raise GuardError(f"'{host}' no tiene direcciones utilizables")

    for ip in addresses:
        if ip.is_loopback or ip.is_private or ip.is_link_local:
            return ip

    raise GuardError(
        f"'{host}' resuelve a {addresses[0]}, que es una dirección pública.\n"
        "  Este laboratorio solo ataca destinos de la red local (RFC1918, loopback,\n"
        "  link-local). Si quieres usar un dominio con contexto, apunta ese nombre a\n"
        "  una IP del lab y resuélvelo con --dns-server o --resolve.\n"
        "  Para medir un servicio tuyo en Internet usa un proveedor de pruebas de\n"
        "  carga con autorización por escrito."
    )


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
        raise GuardError(f"destino invalido: '{value}'")
    if port is None:
        raise GuardError("falta el puerto: usa --port o 'host:puerto'")
    if not 1 <= port <= 65535:
        raise GuardError(f"puerto fuera de rango: {port}")
    return host, port


def clamp(name: str, value: float, maximum: float) -> float:
    """Recorta un parámetro al tope duro e informa cuando lo hace."""
    if value <= 0:
        raise GuardError(f"{name} debe ser > 0 (recibido {value})")
    if value > maximum:
        print(f"[guard] {name}={value} excede el tope del lab, se usa {maximum}")
        return maximum
    return value
