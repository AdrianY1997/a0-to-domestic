# 0004. El planificador es una función pura del dominio

- Estado: aceptada
- Fecha: 2026-10-15 (SPEC §5); WP-02

## Contexto

La UI, la CLI y el motor de render deben calcular exactamente lo mismo: número de hojas, escala, rejilla, teselas
y marcas. Si cada uno tuviera su propia lógica, aparecerían diferencias. Además, el cálculo tiene casos frontera
(p. ej. `T = n·d + o` debe dar exactamente `n` columnas).

## Decisión

`a0td.domain.planner.plan(...)` es una función pura, sin E/S, que recibe la geometría de la página y los ajustes, y
devuelve un `Plan`. Es la única fuente de verdad. El dominio no importa `ingest`, `render`, `api` ni `storage`, y
se verifica con mypy estricto.

Reglas que se fijan en esta decisión:

- `exact_scale` lo decide quien llama (`jobs/exact_scale.py`), no el planificador, que no conoce los presets.
  Oculta las sugerencias de ahorro cuando la escala debe ser exacta.
- Las sugerencias de ahorro solo aparecen en modo `percent`. En los demás modos la escala no es libre.
- Todas las comparaciones de número de teselas usan `ceil_eps(x) = ceil(x - 1e-9)`.
- La orientación automática elige la que da menos hojas (o, si empatan, menor cobertura y vertical).

## Alternativas consideradas

- **Lógica repartida entre UI, CLI y render**: descartada por riesgo de divergencia.
- **Planificador con E/S (leer el PDF dentro)**: impediría probarlo con propiedades (Hypothesis) de forma
  rápida y determinista.

## Consecuencias

- Las invariantes de SPEC §15.2 (cobertura, solape exacto, minimalidad, centrado) se prueban con Hypothesis.
- El render recibe el `Plan` y no recalcula nada. Si cambia el planificador, cambian las salidas: por eso hay tests golden.
- El objetivo de SPEC §13 para `POST /plan` es p95 < 50 ms.
