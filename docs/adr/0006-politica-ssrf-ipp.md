# 0006. Política SSRF para impresoras IPP

- Estado: aceptada
- Fecha: WP-19 (decisión del orquestador); auditoría de WP-19

## Contexto

La impresión directa por IPP (F3) hace que el servidor se conecte a una URI que escribe el usuario. Sin control,
esa URI permitiría SSRF: acceder a `localhost`, a servicios internos o al endpoint de metadatos de una nube. La
auditoría de WP-19 marcó como crítica la falta de esa política.

## Decisión

- El cliente IPP es propio, en Python puro (`printing/ipp.py`, Print-Job y Get-Printer-Attributes). No depende de
  `ipptool`. Se prueba contra un servidor IPP falso (`tests/support/fake_ipp.py`).
- `printing/netpolicy.py` aplica la política a cualquier host que dé el usuario:
  - Se permite la LAN privada.
  - Se rechazan loopback, link-local, metadatos y multicast.
  - Hay una excepción explícita con `A0TD_IPP_ALLOW_HOSTS` (hosts o IPs).
- La IP resuelta se **fija al conectar**, para evitar un cambio de DNS entre la comprobación y la conexión.
- Los errores que ve el cliente son genéricos. El detalle va al log.
- `A0TD_IPP_INSECURE_TLS` desactiva la verificación TLS solo si el usuario lo activa, para impresoras con
  certificado autofirmado.
- La lectura de respuestas tiene tope, y el PDF no se carga entero en memoria.

## Alternativas consideradas

- **Bloquear toda la red privada**: impediría imprimir en la impresora de la casa, que es el caso de uso.
- **Confiar en la URI sin comprobar**: SSRF directo.

## Consecuencias

- `ipp_uri` pasa siempre por la política antes de conectar.
- Una impresora en loopback o de metadatos solo funciona si el administrador la añade a `A0TD_IPP_ALLOW_HOSTS`.
- Las rutas nuevas fuera de `/api/v1` (como `/metrics`) deben protegerse en el middleware de autenticación.
