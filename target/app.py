"""Servidor HTTP victima del laboratorio de resiliencia a DDoS.

Solo usa la libreria estandar para que la imagen sea minima y reproducible.
Modela un servidor con pool de workers acotado: cuando se satura responde 503
en lugar de encolar indefinidamente, que es exactamente lo que queremos medir.
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_INFLIGHT = int(os.getenv("MAX_INFLIGHT", "64"))
WORK_MS = int(os.getenv("WORK_MS", "25"))
STARTED_AT = time.time()

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_lock = threading.Lock()
_stats = {
    "requests": 0,
    "inflight": 0,
    "rejected": 0,
    "errors": 0,
    "duration_sum": 0.0,
    "duration_count": 0,
    "buckets": {b: 0 for b in BUCKETS},
    "inf": 0,
}


def _observe(duration: float) -> None:
    """Registra una medicion en el histograma interno."""
    _stats["duration_sum"] += duration
    _stats["duration_count"] += 1
    for bucket in BUCKETS:
        if duration <= bucket:
            _stats["buckets"][bucket] += 1
            return
    _stats["inf"] += 1


def _render_metrics() -> bytes:
    """Serializa las metricas en formato de texto Prometheus."""
    with _lock:
        snapshot = {
            "requests": _stats["requests"],
            "inflight": _stats["inflight"],
            "rejected": _stats["rejected"],
            "errors": _stats["errors"],
            "duration_sum": _stats["duration_sum"],
            "duration_count": _stats["duration_count"],
            "buckets": dict(_stats["buckets"]),
            "inf": _stats["inf"],
        }

    lines = [
        "# HELP lab_requests_total Peticiones HTTP aceptadas por el worker pool.",
        "# TYPE lab_requests_total counter",
        f"lab_requests_total {snapshot['requests']}",
        "# HELP lab_rejected_total Peticiones rechazadas por saturacion (503).",
        "# TYPE lab_rejected_total counter",
        f"lab_rejected_total {snapshot['rejected']}",
        "# HELP lab_errors_total Peticiones que terminaron en error interno.",
        "# TYPE lab_errors_total counter",
        f"lab_errors_total {snapshot['errors']}",
        "# HELP lab_inflight Peticiones en curso ahora mismo.",
        "# TYPE lab_inflight gauge",
        f"lab_inflight {snapshot['inflight']}",
        "# HELP lab_uptime_seconds Segundos desde el arranque del proceso.",
        "# TYPE lab_uptime_seconds gauge",
        f"lab_uptime_seconds {time.time() - STARTED_AT:.3f}",
        "# HELP lab_request_duration_seconds Latencia de las peticiones atendidas.",
        "# TYPE lab_request_duration_seconds histogram",
    ]
    for bucket in BUCKETS:
        lines.append(
            f'lab_request_duration_seconds_bucket{{le="{bucket}"}} '
            f"{snapshot['buckets'][bucket]}"
        )
    lines.append(f'lab_request_duration_seconds_bucket{{le="+Inf"}} {snapshot["inf"]}')
    lines.append(
        f"lab_request_duration_seconds_sum {snapshot['duration_sum']:.6f}"
    )
    lines.append(
        f"lab_request_duration_seconds_count {snapshot['duration_count']}"
    )
    return ("\n".join(lines) + "\n").encode()


class Handler(BaseHTTPRequestHandler):
    """Atiende los endpoints de la victima."""

    protocol_version = "HTTP/1.1"
    server_version = "lab-target/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - firma impuesta por la stdlib
        """Silencia el log por peticion para no ensuciar la salida."""
        return

    def _send(self, status: int, body: bytes, ctype: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - firma impuesta por la stdlib
        """Despacha GET /, /heavy, /healthz y /metrics."""
        started = time.perf_counter()
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/metrics":
            self._send(200, _render_metrics(), "text/plain; version=0.0.4")
            return

        if path == "/healthz":
            self._send(200, b"ok\n")
            return

        with _lock:
            if _stats["inflight"] >= MAX_INFLIGHT:
                _stats["rejected"] += 1
                saturated = True
            else:
                _stats["inflight"] += 1
                _stats["requests"] += 1
                saturated = False

        if saturated:
            self._send(503, b"worker pool saturated\n")
            return

        try:
            if path == "/heavy":
                query = parse_qs(parsed.query)
                ms = int(query.get("ms", [str(WORK_MS)])[0])
                ms = max(0, min(ms, 2000))
                time.sleep(ms / 1000.0)
                self._send(200, b"work done\n")
            else:
                self._send(200, b"ok\n")
        except Exception:  # noqa: BLE001 - el laboratorio solo cuenta el fallo
            with _lock:
                _stats["errors"] += 1
            raise
        finally:
            elapsed = time.perf_counter() - started
            with _lock:
                _stats["inflight"] -= 1
                _observe(elapsed)


class Server(ThreadingHTTPServer):
    """Servidor con hilos daemon para apagar rapido al recibir SIGTERM."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = int(os.getenv("BACKLOG", "128"))

    def handle_error(self, request, client_address):
        """Silencia los cortes de conexion: son parte normal del escenario."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        print(f"[target] error con {client_address[0]}: {exc!r}", flush=True)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    server = Server(("0.0.0.0", port), Handler)
    print(
        f"target escuchando en :{port} · max_inflight={MAX_INFLIGHT} "
        f"· work_ms={WORK_MS} · backlog={server.request_queue_size}",
        flush=True,
    )
    server.serve_forever()
