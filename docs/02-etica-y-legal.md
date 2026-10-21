# Ética, legalidad y límites

## Lo que dice la ley

Un DDoS es, en la mayoría de ordenamientos, un delito: daño a sistemas ajenos,
interrupción de servicio o acceso no autorizado. En España encaja en los arts.
264 y ss. del Código Penal; en Estados Unidos, en el Computer Fraud and Abuse
Act. **La intención educativa no cambia la tipicidad**: lo que cuenta es el
efecto sobre un sistema del que no eres responsable.

Que una IP sea privada no te da permiso. El servidor de tu vecino, el router de
la oficina o el servicio de otra persona en la misma red siguen siendo ajenos.

## Cómo está acotado este laboratorio

- Solo acepta destinos que resuelvan a **red local**: RFC1918, loopback o
  link-local. Una dirección pública se rechaza antes de abrir un socket
  (`loadgen/guard.py`).
- Por defecto, el atacante vive en una red Docker **interna** sin salida a
  Internet ni a tu LAN. Alcanzar tu red local exige `--lan`, de forma explícita.
- Topes duros: 500 peticiones por ráfaga, 20 contenedores, 400 peticiones
  simultáneas, 1000 sockets.
- Sin suplantación de IP, sin sockets crudos, sin reflexión ni amplificación.
- Cada contenedor es una IP atribuible: no hay anonimización.

Estas barreras son parte del diseño, no un detalle. Si las quitas, el código deja
de ser un laboratorio.

## Uso legítimo

- Enseñar, con números, qué es saturar un servicio y cómo se comporta.
- Practicar en tu propio entorno: contenedores del compose o un servicio tuyo en
  tu red de laboratorio.
- Formarte en defensa: entender qué panel o qué métrica delata una saturación.

## Antes de apuntar a algo que no es el compose

1. **¿Es tuyo?** Si la respuesta no es un sí rotundo, no lo lances.
2. **Autorización por escrito** del propietario del sistema y del proveedor de
   red. Muchos contratos de hosting prohíben explícitamente pruebas de DoS.
3. **Ventana acordada** y contacto técnico disponible durante la prueba.
4. **Alcance definido**: hosts, rutas, intensidad máxima, criterio de parada.
5. **Si es un servicio en Internet**, un proveedor especializado de pruebas de
   carga. Ellos asumen la coordinación con el ISP; tú no.

El documento de alcance y el consentimiento son lo que separa una prueba de
carga de un ataque, no la herramienta.

## Señales de alarma

- Te piden "probar" un sitio que no es suyo, o "solo un ratito".
- El encargo incluye ocultar el origen del tráfico.
- Se pide integración con amplificación o listas de proxies abiertos.
- Se pide repartir la carga entre máquinas que no controlas.

En cualquiera de esos casos: no participar y, si hay un cliente o empleador
detrás, escalarlo.

## Sobre este material

Es docente y no es asesoramiento legal. Si vas a ejecutar pruebas sobre
infraestructura real, consulta a un abogado y al proveedor antes, no después.
