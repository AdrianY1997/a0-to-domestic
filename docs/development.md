# Guía de desarrollo

Esta guía describe la arquitectura, el entorno de desarrollo, las pruebas y la CI. Las reglas de trabajo para
agentes y las trampas conocidas están en [AGENTS.md](AGENTS.md); la especificación completa, en
[SPEC.md](../SPEC.md).

Todo se ejecuta en contenedores. No instales dependencias en el host.

## 1. Arquitectura

### 1.1 Stack

| Capa | Elección |
|---|---|
| Backend | Python 3.13, FastAPI, Uvicorn, Pydantic v2, pydantic-settings |
| PDF | PyMuPDF (imposición vectorial, render, análisis) |
| Raster | pyvips (libvips), Pillow, pillow-heif |
| Otros | ezdxf (CAD), numpy, Typer (CLI), SQLAlchemy 2 + Alembic |
| Conversores externos | LibreOffice, Inkscape, librsvg, Ghostscript, qpdf, LibreDWG, djvulibre |
| Frontend | React, TypeScript, Vite, Mantine, TanStack Query, Zustand, react-i18next, react-zoom-pan-pinch |
| Contrato | OpenAPI de FastAPI → tipos TypeScript con `openapi-typescript` |
| Pruebas | pytest, Hypothesis, Vitest + Testing Library, Playwright |
| Calidad | ruff, mypy, ESLint, Prettier |

### 1.2 Componentes

```
Navegador (SPA React) ◄── SSE ──┐
        │                       │
        ▼                       │
Uvicorn / FastAPI ──────────────┘
  ├─ sirve la SPA estática
  ├─ /api/v1: planificador (dominio puro, en proceso)
  ├─ gestor de trabajos ── 1 subproceso por trabajo ──► worker (ingesta o render)
  └─ SQLite (solo escribe el proceso API)

Conversores: LibreOffice · Inkscape · rsvg · Ghostscript · LibreDWG · ezdxf · ddjvu
Volumen /data: documents/ · jobs/ · tmp/ · fonts/ · inbox/ · outbox/
```

### 1.3 Flujo de datos

```
subida (stream a disco + SHA-256) → documento "processing" → trabajo ingest
  → detect → converter → canónico → analyze (+ ocupación) → preview → documento "ready"
UI ⇄ POST /plan (puro, en proceso) · POST /plan/sheet-preview (render de una hoja)
POST /jobs → trabajo render → plan por página → motor vectorial o raster → marcas → plano de montaje
  → PDF / ZIP en jobs/{id}/output → SSE "done" → descarga o impresión
```

### 1.4 Capas del código

- **`domain/`**: geometría, papeles, ajustes, planificador. Función pura, sin E/S. No importa `ingest`,
  `render`, `api` ni `storage`. Mypy estricto.
- **`ingest/`**: detección de formato, conversores, documento canónico, análisis y mapa de ocupación.
- **`render/`**: motores vectorial y raster, marcas, plano de montaje, salidas. Mypy estricto.
- **`jobs/`**: gestor de subprocesos y worker. El worker recibe la especificación por stdin y nunca toca la base de datos.
- **`storage/`**: modelos, migraciones (Alembic) y archivos en `/data`.
- **`api/`**: solo orquesta. No contiene lógica de dominio.
- **`cli/`**: Typer, sobre los mismos módulos.
- **`printing/`, `automation/`**: impresión IPP y carpeta vigilada.
- **`frontend/src/`**: `features/` por funcionalidad, `api/` (tipos generados), `store/`, `i18n/`.

### 1.5 Mapa del repositorio

| Ruta | Contenido |
|---|---|
| `SPEC.md`, `README.md`, `THIRD_PARTY_LICENSES.md` | Especificación, portada y licencias de terceros |
| `compose.yml` | Uso en producción local (imagen `full`) |
| `compose.dev.yml` | Entorno de desarrollo y perfil `e2e` |
| `.devcontainer/` | Dev Container de VS Code (usa `compose.dev.yml`) |
| `docker/` | `Dockerfile` (todas las etapas), `entrypoint.sh`, plantilla de LibreOffice |
| `backend/src/a0td/` | Código del backend (ver 1.4) |
| `backend/tests/` | `unit/`, `property/`, `integration/`, `api/`, `cli/`, `golden/` (referencias), `support/` |
| `backend/alembic.ini`, `backend/pyproject.toml` | Migraciones y dependencias Python (`uv.lock`) |
| `frontend/` | SPA (Vite). `npm run gen:api` regenera `src/api/schema.d.ts` |
| `e2e/tests/` | Pruebas Playwright contra la imagen `full` |
| `tools/make_fixtures.py` | Generador determinista de fixtures |
| `fixtures/` | Documentos de prueba (`manifest.json`, `malicious/`) |
| `scripts/container-smoke.sh` | Smoke test de las imágenes |
| `.github/workflows/ci.yml` | CI |
| `docs/` | Esta guía, la guía de usuario, ADR, AGENTS y PROGRESS |

## 2. Entorno de desarrollo

### 2.1 Imagen y servicios

```bash
docker build -f docker/Dockerfile --target dev -t a0td:dev .   # solo si cambian dependencias o el Dockerfile
docker compose -f compose.dev.yml up                          # backend + frontend con recarga
```

- `backend`: Uvicorn con `--reload` en el puerto 8000 del host (solo `127.0.0.1`). El código se monta desde
  `./backend`; el entorno virtual vive en `/opt/venv`, fuera del montaje.
- `frontend`: Vite en `http://localhost:5173`, con `/api` redirigido al backend. `node_modules` está en un
  volumen con nombre, para que vaya rápido en Windows.
- `./fixtures` se monta en el backend en solo lectura. Para regenerar fixtures, monta la carpeta en escritura
  de forma temporal.

Si el puerto 8000 está ocupado en tu máquina, cámbialo en `compose.dev.yml` sin commitearlo.

### 2.2 Dependencias

- Python: edita `backend/pyproject.toml`, regenera `uv.lock` y reconstruye `a0td:dev`.
- Node: `frontend/package.json` y `package-lock.json`, con `npm ci` en los contenedores.
- Si cambias dependencias de sistema, reconstruye la imagen `dev`.

### 2.3 Dev Container

Abre el repositorio en VS Code con la extensión *Dev Containers*. Usa `compose.dev.yml`, el servicio `backend`
y el intérprete `/opt/venv/bin/python`.

### 2.4 Windows

Los bind mounts desde NTFS son lentos. Para más velocidad, clona el repositorio dentro de WSL2.
`.gitattributes` fuerza LF (`* text=auto eol=lf`), imprescindible para `entrypoint.sh`.

## 3. Pruebas

### 3.1 Backend

```bash
docker compose -f compose.dev.yml run --rm backend sh -c "pytest -q && ruff check . && ruff format --check . && mypy src/a0td/domain"
```

Cobertura objetivo (SPEC §13): `domain/` ≥ 95 %, total ≥ 80 %. Para regenerar las referencias de golden:
`pytest --update-golden`.

Las pruebas de SSE con `TestClient` deben abrir el stream en un hilo (ver `tests/api/test_hardening.py`).
El render simulado para tests está en `tests/support/fake_render_engine.py`.

### 3.2 Frontend

```bash
docker compose -f compose.dev.yml run --rm frontend npm run lint
docker compose -f compose.dev.yml run --rm frontend npm test
docker compose -f compose.dev.yml run --rm frontend npm run build   # tsc --noEmit + vite build
```

Los textos de interfaz van siempre por i18n. Una clave nueva se añade en `es.json` **y** `en.json` (un test lo exige).

### 3.3 End-to-end (Playwright)

```bash
docker compose -f compose.dev.yml --profile e2e run --rm e2e
```

Levanta `app-full` (imagen `full` endurecida, con sistema de archivos de solo lectura) y ejecuta las especificaciones
de `e2e/tests/`: flujo A0 → A4, una sola hoja (`fit-sheet`), contraseña y raster sin PPP. El navegador usa `en-GB`, porque
el papel por defecto depende de la región. La subida deduplica por SHA-256; `uploadFixture` añade bytes únicos a
los fixtures cifrados para que cada ejecución sea independiente.

### 3.4 Smoke de contenedores

```bash
bash scripts/container-smoke.sh          # REBUILD=0 para reutilizar imágenes
```

Comprueba que `slim`, `full` y `dev` construyen, que `a0td formats` devuelve los conversores esperados por
imagen (el JSON se parsea y se verifica la disponibilidad de cada target), y que el healthcheck pasa con
`read_only: true`.

### 3.5 Pruebas manuales antes de una versión

Imprime la página de calibración, monta A0 → A4 (21 hojas) y mide tres distancias de al menos 500 mm.
El error debe ser ≤ 0.5 % tras calibrar (SPEC §15.8).

## 4. Añadir un conversor

Los formatos se añaden registrando un conversor, sin tocar el núcleo.

1. **Detección:** añade el identificador del formato en `ingest/detect.py` (`DocFormat`) con su firma según el
   Apéndice E de la spec. Las firmas débiles (HPGL, DXF ASCII, SVG) solo se aceptan si la extensión coincide o
   el análisis del contenido es inequívoco.
2. **Conversor:** implementa el protocolo `Converter` (`id`, `handles`, `available()`, `convert()`) en
   `ingest/converters/`. `available()` comprueba los binarios de la imagen; si faltan, el formato devuelve
   `FORMAT_NOT_AVAILABLE_IN_IMAGE`.
3. **Subprocesos:** ejecuta las herramientas externas con `run_tool()` (`converters/base.py`): lista de
   argumentos sin shell, timeout, límite de memoria con `prlimit`, `HOME` y `TMPDIR` dentro de `work/`. No uses
   `preexec_fn`, que no es seguro con hilos.
4. **Registro:** añade el conversor a `default_registry()` (`ingest/converters/registry.py`). `a0td formats` y
   `/api/v1/info` lo listarán.
5. **Imagen:** instala las herramientas en la etapa correcta de `docker/Dockerfile` (`slim` o `full-tools`).
   Verifica los nombres de paquete en Debian trixie (algunos llevan el sufijo `t64`).
6. **Fixture:** añade el archivo de prueba a `tools/make_fixtures.py` y a `fixtures/manifest.json`. Debe ser
   determinista (sin marcas de tiempo ni IDs aleatorios).
7. **Pruebas:** un test de integración por conversor con su fixture: número de páginas, tamaño dentro de
   tolerancia y que la página no sale en blanco. Si el binario no está disponible, el test se salta con `skip`.
   Añade también una muestra maliciosa si el formato lo permite.
8. **Smoke:** amplía las expectativas de `a0td formats` en `scripts/container-smoke.sh` para el target correspondiente.
9. **Códigos de error:** si necesitas códigos nuevos, añádelos a `domain/errors.py` y al mapa de
   `api/errors.py`.
10. **Documentación:** actualiza la tabla de formatos del [README](../README.md#formatos-admitidos) y el
    [manual de usuario](user-guide.md) si cambia lo que el usuario ve.

## 5. Convenciones

Las convenciones completas están en [AGENTS.md](AGENTS.md#convenciones-ya-fijadas). Las más importantes:

- Código, identificadores y comentarios en inglés. Documentación de usuario en español.
- Unidades internas en mm. La conversión a pt solo ocurre en el render.
- Redondeo de teselas con `ceil_eps`, nunca con `ceil` directo.
- Python: ruff (línea 100), tipado completo, Pydantic v2. Sin estado global salvo la configuración.
- Frontend: TypeScript estricto, componentes funcionales, sin textos fijos. `localStorage` siempre dentro de try/catch.
- Cualquier desviación de la spec se documenta en `docs/adr/` y se refleja en `docs/PROGRESS.md`.

### 5.1 Trampas frecuentes

- PyMuPDF: `page.transformation_matrix` pierde el desplazamiento de la CropBox cuando la página está rotada.
  Construye las matrices a mano y prueba rotación y CropBox desplazada juntas.
- Un límite que rebaja la resolución (tope de píxeles, memoria) debe propagar los PPP efectivos. Nunca
  etiquetes con los PPP pedidos.
- `papers.oriented()` siempre ordena ancho y alto: no lo uses para rollos.
- Las variables de entorno vacías (`A0TD_X=""`) deben convertirse en `None` en los campos `Path | None`.
- No reinicies Docker Desktop: puede haber otros contenedores en marcha.

## 6. CI y publicación

El workflow `.github/workflows/ci.yml` tiene estos trabajos:

1. **`backend`**: construye la imagen `dev` y ejecuta ruff, mypy y pytest dentro de ella; `pip-audit`.
2. **`frontend`**: lint, pruebas y build; `npm audit`.
3. **`images`** (necesita `backend` y `frontend`): construye `slim` y `full` para `linux/amd64` y `linux/arm64`,
   escanea con Trivy (falla con HIGH/CRITICAL corregibles), genera SBOM SPDX con syft, publica en GHCR, y
   ejecuta el E2E y el smoke contra la imagen `full`.

La versión llega a la imagen como build arg `A0TD_VERSION` y se expone en `/api/v1/info`. La versión del paquete
está en `backend/pyproject.toml`.

### 6.1 Pendientes conocidos

Consulta [PROGRESS.md](PROGRESS.md) para el detalle. Entre ellos: `npm run gen:api` requiere el backend en marcha;
la etiqueta de la imagen de Playwright es provisional; y `/api/v1/info` debe reportar `A0TD_VERSION` en todos los casos.
