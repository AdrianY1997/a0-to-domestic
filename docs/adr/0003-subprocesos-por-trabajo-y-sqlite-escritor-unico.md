# 0003. Un subproceso por trabajo y SQLite con un único escritor

- Estado: aceptada
- Fecha: 2026-10-15 (SPEC §9.5); WP-08 y WP-06

## Contexto

La ingesta y el render pueden tardar minutos, consumir mucha memoria y ejecutar conversores que fallan o se
cuelgan (LibreOffice, Ghostscript). Un fallo no debe tumbar la API. Tampoco se quiere un servicio extra de cola:
la aplicación es local y de un solo usuario.

## Decisión

- Cada trabajo (`ingest` o `render`) es un subproceso `python -m a0td.jobs.worker`. El gestor limita la
  concurrencia con un semáforo (`A0TD_WORKERS`), aplica timeout (`A0TD_JOB_TIMEOUT_S`) y límite de memoria
  (`A0TD_JOB_MEMORY_MB`), y pone `nice`.
- La especificación del trabajo llega al worker **por stdin**. Así la contraseña del PDF nunca toca el disco.
- El worker escribe progreso como líneas JSON por stdout. El gestor es el **único escritor** de SQLite y reenvía
  el progreso por SSE.
- Cancelar envía SIGTERM al grupo de procesos (incluido LibreOffice) y SIGKILL a los 5 s.
- Al arrancar, los trabajos `running` pasan a `failed` con `INTERRUPTED` y los `queued` se reencolan.
- Los límites de memoria de los conversores usan `prlimit` delante del argv, no `preexec_fn` (no es seguro con hilos).
- La interfaz `JobQueue` es abstracta, por si algún día se quiere Redis o RQ.

## Alternativas consideradas

- **Cola externa (Redis/RQ)**: innecesaria para un despliegue local de un usuario.
- **Hilos dentro de la API**: un conversor colgado o una bomba de memoria afectaría a toda la API.

## Consecuencias

- Los tests de SSE deben abrir el stream en un hilo, porque `TestClient` solo devuelve al terminar.
- Los fallos de un trabajo quedan aislados y se muestran como `error` con código.
- Escribir en SQLite desde varios procesos está prohibido: solo la API escribe. Los workers solo emiten JSON.
