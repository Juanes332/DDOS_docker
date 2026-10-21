# Ciberseguridad · pruebas de carga, resiliencia y simulación de DoS

Herramienta Docker para generar carga de alto volumen contra un servicio y medir
su comportamiento: cuántas peticiones aguanta, cuándo empieza a responder `503`,
cuántas conexiones sostiene y cómo reacciona su capa de autenticación.

Se usa en ejercicios de **pentesting autorizado** y en pruebas de **capacidad y
resiliencia**: flood de ráfagas HTTP, agotamiento de conexiones y slowloris. Se
configura con **DNS + puerto**, **cuántas peticiones por ráfaga** y **cada cuántos
segundos** sale esa ráfaga. Puede correr indefinidamente.

```bash
make up
scripts/attack.sh --dns target --port 8000 --requests 100 --every 1 --forever
```

## Política de seguridad y uso autorizado

Esta herramienta genera tráfico de alto volumen y puede saturar un servicio. Su
uso está permitido **únicamente** contra:

1. Sistemas de tu propiedad.
2. Sistemas para los que tengas **autorización por escrito** del propietario y,
   cuando aplique, del proveedor de hosting/red.

**Está prohibido** apuntarla a cualquier sistema del que no seas responsable o
del que no tengas autorización, aunque la IP sea privada, aunque sea "solo para
probar", aunque el destino sea de un conocido o de la misma red.

### Legal

Lanzar un ataque de denegación de servicio (DDoS) es delito en la mayoría de
ordenamientos. Ejemplos:

- España: arts. 264 y siguientes del Código Penal.
- Colombia: Ley 1273 de 2009 (delitos informáticos).
- Estados Unidos: Computer Fraud and Abuse Act (18 U.S.C. § 1030).

La intención educativa o de "prueba" no elimina la responsabilidad: lo que
cuenta es el efecto sobre el sistema de un tercero.

### Antes de lanzar nada

1. **¿Es tuyo?** Si la respuesta no es un "sí" rotundo, no lo lances.
2. **Autorización por escrito** del propietario del sistema y, si aplica, del
   proveedor de red/hosting. Muchos contratos de hosting prohíben explícitamente
   las pruebas de denegación de servicio.
3. **Alcance definido**: hosts, rutas, intensidad máxima, ventana acordada y
   criterio de parada.
4. Para medir un servicio en Internet, usa un **proveedor de pruebas de carga**
   que coordine con el ISP, no este flood de ráfagas.

### Reportar uso indebido

Si ves este repositorio o esta herramienta usados contra sistemas no autorizados,
repórtalo al propietario del repositorio o a las autoridades competentes. No
participes ni ayudes a amplificar un ataque contra terceros.

Detalles ampliados en `docs/02-etica-y-legal.md`.

## Instalación

Requisitos: Docker con Compose v2, `make`, `python3`, `curl`.

```bash
git clone git@github.com:Juanes332/DDOS_docker.git
cd DDOS_docker
make up          # víctima en http://<IP-del-host>:8000
make demo        # 3 contenedores · 100 peticiones cada 1 s · infinito
```

`Ctrl+C` detiene los contenedores atacantes y deja la víctima viva.

## Los parámetros

| Bandera | Qué hace | Defecto |
|---|---|---|
| `--dns HOST` | nombre DNS (o IP) del objetivo | obligatorio |
| `--port P` | puerto del objetivo (def. 80, o 443 con `--https`) | 80/443 |
| `--requests N` | peticiones **por ráfaga y por contenedor** | 50 |
| `--every S` | segundos entre ráfagas | 1 |
| `--containers N` | cuántos contenedores atacan a la vez (sin tope) | 1 |
| `--forever` | no termina hasta `Ctrl+C` | finito |
| `--duration S` | duración si no usas `--forever` | 30 |
| `--vector V` | `http`, `slowloris`, `conn`, `mixed` | http |
| `--path RUTA` | ruta HTTP | `/` |
| `--preset P` | `light` (25/s), `medium` (100/s), `hard` (250/s) | — |
| `--lan` | red extra con salida (hoy redundante: `lab` ya sale) | con salida |
| `--https` | habla TLS (se activa solo con `--port 443`) | según puerto |
| `--dns-server IP` | servidor DNS que usan los contenedores | el de Docker |
| `--resolve NOMBRE=IP` | fija un nombre a una IP (repetible) | — |
| `--dry-run` | muestra el plan y no lanza nada | — |
| `--detach` | lanza los contenedores y sale (quedan en segundo plano) | — |

La carga total es `N ÷ S × contenedores` peticiones por segundo. Ejemplos:

```bash
# 3 contenedores × 100 peticiones cada 1s = 300 req/s, infinito
scripts/attack.sh --dns target --port 8000 --requests 100 --every 1 --containers 3 --forever

# ráfagas de 500 cada 5 segundos desde 2 contenedores = 200 req/s
scripts/attack.sh --dns target --port 8000 --requests 500 --every 5 --containers 2 --forever

# 20 segundos de prueba y resumen final
scripts/attack.sh --dns target --port 8000 --requests 200 --every 0.5 --duration 20
```

Atajos equivalentes:

```bash
make forever N=3 REQUESTS=100 EVERY=1
make attack ARGS="--dns target --port 8000 --requests 200 --every 0.5 --duration 20"
```

## Qué ves mientras corre

Una línea por ráfaga y por contenedor:

```
[w1]   rafaga    12 · 100 enviadas · 200:84 503:16                    · p95   48.3 ms
[w1]   rafaga    13 · 100 enviadas · 200:73 503:27                    · p95   96.1 ms
```

- **`200`**: la víctima atendió la petición.
- **`503`**: su pool de trabajadores está lleno; empieza a rechazar.
- **`sin-resp`**: no hubo respuesta (timeout o reset de conexión).

El contador `[wN]` identifica el contenedor, es decir, la IP de origen. Con
`--containers 5` ves cinco secuencias distintas atacando a la vez.

## DNS con contexto (dominio real, tráfico local)

El destino se escribe como nombre DNS, así que puedes usar un dominio con
contexto (`victima.midominio.lab`, `app.empresa.test`…) en lugar de una IP.
Dos formas de resolverlo:

**1. Con tu servidor DNS** (el que tiene la zona):

```bash
scripts/attack.sh --dns victima.midominio.lab --port 8000 \
                  --dns-server 192.168.1.10 --lan \
                  --requests 100 --every 1 --forever
```

**2. Fijando el nombre a una IP del lab**, sin montar DNS:

```bash
scripts/attack.sh --dns victima.midominio.lab --port 8000 \
                  --resolve victima.midominio.lab=172.28.0.2 \
                  --requests 100 --every 1 --forever
```

`--resolve` es repetible. Ambas opciones se aplican solo al contenedor atacante,
mediante un overlay temporal de Compose que se borra al terminar.

En los dos casos el nombre aparece en el `Host` de cada petición, así que para
los estudiantes (y para los logs de la víctima) es un dominio normal; lo que
cambia es a dónde apunta: la IP que fijes o que resuelva tu DNS.

### Puerto 443

Con `--port 443` el generador habla TLS automáticamente (y acepta certificados
self-signed; usa `--https` en otros puertos TLS). Ejemplo completo, sin
`--resolve`, con tu DNS resolviendo el nombre:

```bash
scripts/attack.sh \
  --dns victima.midominio.lab --port 443 \
  --dns-server 192.168.1.10 --lan \
  --requests 100 --every 1 --containers 3 \
  --forever
```

Si el nombre no resuelve dentro del contenedor, el script aborta antes de enviar
nada, así que no hay sorpresas a mitad del ataque. La víctima del lab sirve HTTP
en `8000`; para un 443 real apunta a tu propio servicio con TLS (self-signed o
CA interna, se aceptan ambos por defecto).

## Apuntar a tu propio servicio

El destino se resuelve por DNS y se acepta cualquier dirección: puedes apuntar
a un host de tu LAN o a un servicio en Internet (usa `--lan` para tener salida
de red). Tú eres responsable de contar con autorización para el destino.

**Dentro del entorno local**:

```bash
scripts/attack.sh --dns target --port 8000 --requests 100 --every 1 --forever
```

**Contra un host de tu LAN** (tu DNS local, tu servidor de prácticas):

```bash
make lan DNS=victima.local PORT=80 N=2 REQUESTS=50 EVERY=1
# o directamente:
scripts/attack.sh --dns victima.local --port 80 --requests 50 --every 1 --lan --forever
```

`--lan` añade al contenedor atacante una red con salida (`docker-compose.lan.yml`),
necesaria para que Docker pueda resolver y alcanzar nombres de tu LAN. Sin esa
bandera, el atacante solo existe dentro de `172.28.0.0/16`.

## Límites de la herramienta

Los límites están definidos en cada script de `loadgen/` (y el tope de
contenedores en `scripts/attack.sh`):

```python
MAX_REQUESTS = 500       # peticiones por ráfaga
MAX_CONCURRENCY = 400    # peticiones simultáneas
MAX_SOCKETS = 1000       # slowloris
MAX_CONNECTIONS = 500    # cola TCP
```

No hay tope de contenedores: `--containers` acepta cualquier entero ≥ 1 y el
límite real es la CPU y la memoria de tu máquina. Cada contenedor está limitado
a 1 vCPU y 256 MB en `docker-compose.yml`. Si pides más intensidad por
contenedor, se recorta y se avisa.

## Estructura

```
loadforge/
├── docker-compose.yml        # servicio objetivo + red con salida
├── docker-compose.lan.yml    # overlay opcional para tu LAN
├── Makefile
├── target/                   # víctima: HTTP con pool acotado
├── loadgen/
│   ├── http_flood.py         # ráfagas HTTP (vector principal)
│   ├── slowloris.py          # cabeceras incompletas
│   └── conn_exhaust.py       # agotamiento de cola TCP
├── scripts/attack.sh         # orquestador multi-contenedor
└── docs/
    ├── 01-como-funciona-un-ddos.md
    └── 02-etica-y-legal.md
```

## Uso recomendado

1. `make up` y abre <http://<IP-del-host>:8000> en el navegador.
2. `make demo`: 3 contenedores a 100 req/s. La víctima aún responde `200`.
3. Sube a `make forever N=3 REQUESTS=250 EVERY=1` (750 req/s) y mira aparecer los
   `503` en la consola y en las métricas de la víctima:

   ```bash
   curl -s http://127.0.0.1:8000/metrics | grep -E "loadforge_(requests|rejected|inflight)"
   ```

4. `docker stats` para ver el coste en CPU de cada contenedor atacante.
5. `Ctrl+C` y compara: las peticiones acumuladas, el reparto de códigos y el pico
   de concurrencia.

Guion ampliado en `docs/01-como-funciona-un-ddos.md`.

## Alcance

Herramienta de ciberseguridad para pruebas autorizadas. No incluye suplantación
de IP, sockets crudos, reflexión ni amplificación. Atacar un sistema del que no
eres responsable es un delito: úsalo con tu propia infraestructura o con
autorización por escrito. Detalles en `docs/02-etica-y-legal.md`.
