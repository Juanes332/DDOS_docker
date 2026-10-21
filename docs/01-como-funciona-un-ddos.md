# Cómo funciona una denegación de servicio

Un DDoS no "rompe" el servidor: le quita los recursos que necesita para atender.
Cambia el recurso que se agota y cambia el nombre del ataque. Con esa idea se
explica todo el laboratorio.

## Los tres planos

**Volumétrico (capa 3).** Se satura el enlace: gigabits por segundo que el
operador no puede cursar. Contra esto no hay configuración de servidor que
valga; se resuelve en la red.

**Protocolo (capa 4).** Se agotan tablas de estado: conexiones por segundo, SYN
pendientes, traducciones NAT, límites del balanceador. El ancho de banda suele
ser modesto; el daño es de contadores. Vector `conn`.

**Aplicación (capa 7).** Se agotan trabajadores, hilos, conexiones a base de
datos o CPU con peticiones que parecen legítimas. Es el más barato para el
atacante y el más difícil de separar del tráfico real. Vectores `http` y
`slowloris`.

## El patrón de ráfagas

El generador no envía un flujo continuo: lanza **N peticiones** de golpe y
espera **S segundos**. Eso es lo que en la vida real se llama ataque por ráfagas
y tiene dos consecuencias didácticas:

1. El ritmo medio es `N ÷ S`, pero la **presión instantánea** es N. Una víctima
   con 16 trabajadores que recibe 100 peticiones a la vez encola o rechaza.
2. Entre ráfagas el servidor respira. Si la víctima se recupera en ese hueco, el
   `503` no llega nunca; si no, la cola crece. Cambiar `--every` de 5 a 0,5
   enseña esa diferencia mejor que cualquier diapositiva.

En el laboratorio, `N` está topado a 500 por ráfaga y la concurrencia real a 400
peticiones simultáneas por contenedor.

## Anatomía de cada vector

### `http` — flood de capa 7

Pide `--path` (por defecto `/`) a la víctima. Cada petición cuesta `WORK_MS` de
trabajo simulado. Con `MAX_INFLIGHT` trabajadores, el techo teórico es
`MAX_INFLIGHT ÷ (WORK_MS/1000)` peticiones por segundo; a partir de ahí la
víctima responde `503` en lugar de encolar indefinidamente (*fail fast*).

Con los valores por defecto (16 trabajadores, 100 ms) el techo ronda las
160 req/s. Por eso `--requests 100 --every 1` pasa entero y `--requests 250
--every 1` empieza a dejar `503`: estás cruzando la capacidad real, que es una
curva, no un número mágico.

### `slowloris` — el ataque paciente

Abre conexiones y envía `GET / HTTP/1.1` más una cabecera a medias, sin la línea
final en blanco. Nunca completa la petición, pero mantiene el socket vivo.

En un servidor con trabajadores fijos (gunicorn, PHP-FPM, un pool de hilos), cada
socket colgado retiene un trabajador: decenas de sockets inutilizan el servicio
con apenas unos KB/s. La víctima de este laboratorio usa un hilo por conexión, así
que el coste aparece como hilos y descriptores; el principio es el mismo.

El vector no tiene ráfagas: abre `--requests` sockets y los mantiene. Con
`--forever` los refresca indefinidamente.

### `conn` — agotamiento de cola TCP

Abre conexiones TCP reales y no pide nada. Cada conexión ocupa una entrada en la
tabla de conexiones del sistema y, si el `accept()` no las recoge, un hueco en la
cola de aceptación (`BACKLOG`). Es el mismo cuello de botella que explotaría un
SYN flood, sin falsificar nada: por eso el efecto observable es idéntico y la
atribución es trivial.

### `mixed` — varios planos a la vez

Reparte los contenedores entre los tres vectores. En la realidad los ataques
combinan planos para saturar la defensa en varios frentes; aquí sirve para ver
que el `503` aparece antes cuando además hay sockets colgados.

## Qué mirar en la víctima

```bash
curl -s http://127.0.0.1:8000/metrics | grep -E "loadforge_(requests|rejected|inflight|request_duration)"
```

- `loadforge_requests_total` sube y `loadforge_rejected_total` se queda plano → la víctima
  va sobrada.
- `loadforge_rejected_total` sube → el pool está lleno; es el `503` que ves en consola.
- `loadforge_inflight` pegado a `MAX_INFLIGHT` → saturación sostenida.
- `loadforge_request_duration_seconds` → cuánto tarda lo que sí se atiende.

## Lo que este laboratorio no reproduce

- **Internet real**: latencia variable, pérdida, MTU, NAT del ISP.
- **Botnets**: miles de IPs distintas. Aquí cada contenedor es una IP, así que
  aproximas el efecto con `--containers`, pero una IP de datacenter no se
  comporta como 10 000 dispositivos domésticos.
- **Amplificación/reflexión**: requiere falsificar el origen y meter tráfico en
  Internet a nombre de otra persona. No está implementado, y no es necesario para
  entender el efecto en la víctima.

## Ejercicios

1. Con `--requests 100 --every 1`, sube `--containers` de 1 a 5. ¿En qué punto
   aparecen los primeros `503`?
2. Fija 300 peticiones totales: repártelas como `300 cada 1s` y como `60 cada
   0,2s`. ¿Cambia el resultado? ¿Por qué?
3. Lanza `--vector conn` con 400 conexiones y luego 800. ¿Dónde está el límite
   de la cola (`BACKLOG`)?
4. Baja `MAX_INFLIGHT` a 4 en `docker-compose.yml`, `make up`, y repite. ¿Cuánto
   tarda en rendirse?
5. Observa `docker stats` durante el ataque: ¿cuánta CPU cuesta generar 500
   req/s frente a lo que cuesta servirlas?
