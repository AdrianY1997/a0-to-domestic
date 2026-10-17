# Manual para agentes implementadores

Lee esto **antes** de tocar código. La fuente de verdad funcional es [SPEC.md](../SPEC.md); el estado y las
desviaciones aprobadas están en [PROGRESS.md](PROGRESS.md). Si algo de aquí contradice la spec, manda lo
que diga PROGRESS.md (es más reciente) y avisa al orquestador.

## Reglas de trabajo

1. Toca **solo** los archivos de tu paquete de trabajo (WP). Otros agentes trabajan en paralelo.
2. No modifiques `.claude/`, `.recall/`, `SPEC.md`, ni los `docs/*.md` del orquestador. Si la spec está mal
   o es ambigua, **no la reinterpretes en silencio**: elige lo razonable y repórtalo como desviación.
3. Reutiliza lo que ya existe (lee el código del dominio antes de crear tipos nuevos). Nada de duplicar modelos.
4. Todo se ejecuta en Docker. Nada de instalar dependencias en el host.
5. Formatea y lintéa **solo tus archivos** (`ruff format <tus rutas>`), nunca `ruff format .`, porque
   hay otros agentes editando en paralelo. Para comprobar, usa `ruff format --check .`.
6. El proyecto es un repo git público (https://github.com/AdrianY1997/a0-to-domestic, AGPL-3.0). **Nunca
   hagas commit, push ni cambies de rama**: los commits los hace el orquestador. `.claude/` y `spike/` están
   en `.gitignore`.
7. Informe final breve: archivos, resultado de tests, desviaciones con su porqué. Sin volcar código.

## Comandos

En Git Bash, anteponer `MSYS_NO_PATHCONV=1` si las rutas se deforman.

**Preferente (WP-03 hecho):** la imagen `dev` trae todas las herramientas (LibreOffice, Inkscape, gs, rsvg,
qpdf, ddjvu, dwg2dxf, libvips) y el venv en `/opt/venv` (fuera del montaje).

```
docker build -f docker/Dockerfile --target dev -t a0td:dev .        # solo si cambian dependencias o Dockerfile
docker compose -f compose.dev.yml run --rm backend sh -c "pytest -q && ruff check . && ruff format --check . && mypy src/a0td/domain"
docker compose -f compose.dev.yml up                                 # backend :8000 (reload) + Vite :5173
```

Si añades dependencias Python: edita `pyproject.toml`, regenera `uv.lock` y reconstruye `a0td:dev`.
Dependencias de desarrollo actuales: `python-pptx` y `python-docx` (fixtures F2). Si faltan en la imagen, reconstrúyela.
Para regenerar fixtures, monta `./fixtures` en lectura-escritura (en `compose.dev.yml` está en solo lectura).
Imagen final: `docker build -f docker/Dockerfile -t a0-to-domestic .` (target `full` por defecto).

Alternativa ligera (sin herramientas externas), backend en contenedor desechable:

```
MSYS_NO_PATHCONV=1 docker run --rm -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
  -v "E:/Proyectos/a0-to-domestic/backend:/w" -w /w python:3.13-slim-trixie \
  sh -c "pip install -q uv && uv sync --all-groups && uv run pytest -q && uv run ruff check . \
         && uv run ruff format --check . && uv run mypy src/a0td/domain"
```

Frontend:

```
MSYS_NO_PATHCONV=1 docker run --rm -v "E:/Proyectos/a0-to-domestic/frontend:/w" \
  -v a0td-fe-nm:/w/node_modules -w /w node:24-slim \
  sh -c "npm ci && npm run lint && npm test && npm run build"
```

Cuando WP-03 esté terminado se usarán los comandos canónicos de SPEC §12.3 (`compose.dev.yml`); este
manual se actualizará.

## Mapa del código (estado actual)

| Ruta | Qué hay |
|---|---|
| `backend/src/a0td/config.py` | `Settings` (pydantic-settings), todas las `A0TD_*` de §12.4 + `A0TD_STATIC_DIR` (carpeta de la SPA) |
| `backend/src/a0td/domain/units.py` | Conversión mm/pt/in, `ceil_eps` |
| `backend/src/a0td/domain/geometry.py` | `Rect` en mm |
| `backend/src/a0td/domain/papers.py` | Catálogo (Apéndice A) y emparejado con tamaño normalizado |
| `backend/src/a0td/domain/settings.py` | `JobSettings` (Apéndice B), `Margins` (con `landscape_safe`), `PrinterProfile`, `Preset`, perfiles y presets integrados |
| `backend/src/a0td/domain/naming.py` | IDs de hoja (A1, 2-3, secuencial, prefijo P{n}-) |
| `backend/src/a0td/domain/errors.py` | Enums de códigos de error y aviso, `DomainError(code, params)` |
| `backend/src/a0td/domain/planner.py` | `plan(page, settings, *, printer, occupancy, include_alternatives, exact_scale, max_sheets) -> Plan`; `printer_correction_matrix(page_w, page_h, kx, ky)` (afín PDF a,b,c,d,e,f); `scale_label()`. Sin `printer` usa `target.printer_id` → genérica 5 mm |
| `backend/src/a0td/domain/plan.py` | `Plan`, `SheetPlan` (campos §11.3 + `content_origin_mm`) |
| `backend/src/a0td/domain/page.py` | `PageGeometry`, `ViewTransform` (canónico ↔ vista, rotación horaria) |
| `backend/src/a0td/domain/occupancy.py` | Protocolo `OccupancyMap` (`any_in`, `content_bbox`, **coordenadas de página canónica**), `RectOccupancy`, `GridOccupancy` |
| `backend/src/a0td/ingest/` | `ingest_document(src, workdir, *, password, progress, config, registry, filename, size_override) -> IngestResult` (`.save()`/`.load(workdir)`); `set_physical_size(workdir, i, paper\|width_mm\|height_mm\|dpi)`; `detect()`/`detect_or_raise()`; `ConverterRegistry`/`default_registry()`; `run_tool()` (subprocesos seguros, en `converters/base.py`); `Page.to_geometry(page_count)`; `load_occupancy(page_dir, page, sensitivity)` → `PixelOccupancy` |
| `backend/src/a0td/ingest/detect.py` | `DocFormat` (37 ids: pdf, png, jpeg, tiff, gif, bmp, webp, heif, avif, jp2, ps, eps, ai, svg, svgz, docx, xlsx, pptx, vsdx, odt, ods, odp, odg, doc, xls, ppt, vsd, pub, rtf, emf, wmf, xps, dwg, dxf, cdr, hpgl, djvu). `.docm` y otros con macros → `UNSUPPORTED_FORMAT` |
| `backend/src/a0td/ingest/converters/` | `pdf`, `raster`, `office` (Office/ODF/emf/wmf; perfil LibreOffice por trabajo con macros desactivadas; fuentes vía `FONTCONFIG_FILE`), `svg` (sanea con defusedxml; rsvg → Inkscape), `postscript` (gs `-dSAFER`; ai PDF → ruta pdf), `xps`, `heif` (heif/avif/jp2), `common.py` |
| `backend/src/a0td/ingest/converters/{cad,hpgl,djvu}.py` | Conversores `dxf` y `dwg` (ezdxf; LibreDWG `dwg2dxf`, ODA opcional), HPGL (extensión de Inkscape con marco calculado), DjVu (`ddjvu` → raster). Opciones CAD (`layout`, `units`, `scale`, `color`) por API (`POST /documents`, `/documents/{id}/cad-options`) y CLI (`--cad-*`); llegan al worker en `options_json`. Fixtures con `PYTHONHASHSEED=0` |
| `backend/src/a0td/ingest/vector_size.py` | Reescala `canonical.pdf` de SVG sin unidades; sidecar `native-size.json` |
| `backend/src/a0td/render/` | `engine.py` (`render_job`, `render_sheet_preview`), `vector.py`, `raster.py`, `marks.py`, `assembly.py`, `outputs.py`, `canvas.py`, `geometry.py`, `models.py`, `strings.py` (textos es/en). mypy estricto |
| `backend/src/a0td/cli/main.py` | Typer: `formats`, `info`, `plan`, `tile`, `fit`, `calibration-page` (`--json` en casi todos). Fusión: defaults → `--preset` → `--settings` → flags. Errores: `error [CODE]: …` en stderr, salida 1 |
| `backend/src/a0td/render/color.py` | Grises con gs (caché en `<doc>/cache/canonical-gray.pdf`), rasterizado con PyMuPDF a `output.dpi`, B/N, invertir y blanquear con pyvips → motor raster (WP-15) |
| `backend/src/a0td/{domain,render}/calibration.py`, `api/routers/printers.py` | Cálculo de k/márgenes y PDF de calibración; CRUD de impresoras (integradas → 403 `PRINTER_READONLY`); `borderless` y papeles en claves reservadas de `defaults_json` |
| `frontend/src/features/printers/`, `frontend/src/lib/region.ts` | Lista, formulario y asistente de calibración; papel y unidades por defecto según región (tests fijan `es-ES`) |
| `backend/src/a0td/printing/{ipp,submit}.py` | Cliente IPP en Python puro (Print-Job, Get-Printer-Attributes), rangos de páginas, recurso a PWG Raster con gs. Activado por `A0TD_ENABLE_IPP`. Servidor falso para tests en `tests/support/fake_ipp.py` |
| `backend/src/a0td/automation/hotfolder.py` | Carpeta vigilada por sondeo (`A0TD_HOTFOLDER`, `A0TD_HOTFOLDER_INTERVAL_S`); un archivo se procesa cuando tamaño y mtime coinciden en dos sondeos |
| `.github/workflows/ci.yml` | Lint y tests en la imagen `dev`, auditorías, build multi-arch, Trivy, SBOM, E2E y smoke |
| `backend/src/a0td/render/{layers,ink}.py`, `ingest/stats.py`, `domain/legibility.py` | Capas OCG en la salida (las ocultas, OFF para View, Print y Export) y tinta estimada (`JobOut.ink`); estadísticas por página (texto mín/p10, línea más fina, `detected_scale`); avisos `SMALL_TEXT`/`THIN_LINES` tras escalar. El espejo va en `ViewTransform.mirror` |
| `backend/tests/` | `unit/`, `property/`, `integration/`, `golden/` (refs en `golden/refs`, regenerar con `pytest --update-golden`), `api/`, `conftest.py`, `render_support.py` (helpers compartidos de render/reensamblado) |
| `backend/src/a0td/storage/` | Modelos SQLAlchemy (§11.1 + `jobs.options_json`, `jobs.warnings_json`, `*.error_params_json`), Alembic (`backend/alembic.ini`), `files.py` (layout §11.2), `retention.py` |
| `backend/src/a0td/jobs/` | `manager.py` (subproceso por trabajo, semáforo, SSE, recuperación), `worker.py` (recibe la especificación del trabajo **por stdin**, nunca toca la BD), `exact_scale.py::is_exact_scale(settings, preset_id)` |
| `backend/src/a0td/api/routers/` | Endpoints F1 de §10 + `POST /api/v1/storage/purge {scope: expired\|all}`. `ProblemError` en `api/errors.py` para errores HTTP sin `DomainError` (p. ej. 501 `RENDER_UNAVAILABLE`) |
| `backend/src/a0td/api/app.py` | Factoría: `uvicorn a0td.api.app:create_app --factory` |
| `backend/src/a0td/api/errors.py` | Manejadores problem+json; mapa código → estado HTTP |
| `frontend/src/app/` | Providers, Layout, rutas (lazy), `theme.ts` |
| `frontend/src/features/` | home, editor, result, help, settings (F1). Editor: vista previa + overlay SVG en mm de vista, ajustes, barra de resumen, diálogos de contraseña y de tamaño físico, vista de hoja |
| `frontend/src/lib/tiling.ts` | Rangos de hojas ("A1, B3-B5" ↔ lista), mapeo píxel→mm, acotado del arrastre (WP-14) |
| `frontend/src/features/editor/{TilingTools,AlternativesPanel}.tsx` | Herramientas Mover/Rejilla/Región; panel de alternativas y ahorro |
| `frontend/src/store/` | Zustand: ajustes del editor por documento, persistidos en `localStorage` |
| `frontend/src/api/` | `schema.d.ts` **generado** (`npm run gen:api`, usa `API_URL`), `types.ts` derivado, `client.ts`; subida con XHR (progreso) |
| `frontend/src/api/client.ts` | `api.get/post/put/delete`, `ApiError {code,status,params}` |
| `frontend/src/lib/units.ts` | Conversión y formato de longitudes por idioma |
| `frontend/src/i18n/{es,en}.json` | Claves de UI (un test exige las mismas claves en ambos) |

## Contratos entre paquetes

**Documento ingerido (directorio de documento).** Índices de página **base 0**:
`canonical.pdf` | `page-{i}.tif`, `pages/{i}/{preview.webp, thumb.webp, occupancy.png, occupancy-strict.png,
occupancy-tolerant.png}`, `meta.json` (= `IngestResult`). En el canónico PDF la MediaBox conserva el margen
original y el contenido va envuelto en una matriz `cm`; Trim/Bleed solo en `meta.json`. Se eliminan del
catálogo `Names`, `AF`, `OpenAction`, `AA`, `AcroForm`. Avisos de documento: `ANNOTATIONS_FLATTENED`,
`PDF_REPAIRED`; de página: tamaño y capas (`NONSTANDARD_SIZE`/`LAYERS_PRESENT` con severidad `info`).

**Render (WP-07, lo consume WP-08):**

```python
a0td.render.engine.render_job(doc_dir: Path, settings: JobSettings, out_dir: Path, *,
    printer: PrinterProfile | None = None, exact_scale: bool = False, test_sheet_only: bool = False,
    progress: Callable[[str, float, str, dict], None] | None = None) -> RenderResult
#   kwargs extra: doc_name=None (por defecto de document.json), locale="es", now=None
# RenderResult = {outputs: [OutputFile{name, kind: "pdf"|"zip", size_bytes, pages}], plans: [Plan],
#                 warnings: [RenderWarning{code, severity, params}]}
# Cancelación desde el callback de progreso: lanzar DomainError (se propaga intacto).
a0td.render.engine.render_sheet_preview(doc_dir, settings, page_index, sheet_id, max_px) -> bytes  # PNG
```

`exact_scale=True` (oculta sugerencias de ahorro) lo decide quien llama
(`jobs/exact_scale.py`): preset `builtin-sewing-a4`, o `percent` 100 con unión `butt`.

- `OutputFile.name`: solo nombre base; el worker sanea y renombra si no.
- Nombre visible del documento (para `{name}`): `<doc_dir>/document.json` (`{"id","name"}`).
- `A0TD_RENDER_ENGINE` (env) permite sustituir el módulo de render en tests (por defecto `a0td.render.engine`).

**API para el frontend (WP-08):**

- Índices de página `n` base 0 en todas las rutas.
- `POST /plan`, `/plan/sheet-preview` y `/jobs` toman el documento de `settings.document_id` y aceptan
  `preset_id` opcional. `POST /jobs` valida con el planificador antes de encolar.
- SSE de documento y trabajo: `progress {stage, pct 0–100, message_key, params}`, y terminan con `done`
  `{status, document|job}` o `error` `{code, detail, params}`.
- Contraseña incorrecta → 202 y el documento queda `password_required` con `error_code=PASSWORD_INVALID`.
- `DocumentOut.job_id` = trabajo de ingesta. `GET /jobs` lista `kind=render` por defecto (`?kind=all` para todos).
- `DELETE` de documento o trabajo cancela antes los trabajos en curso.
- `compose.dev.yml` monta `./fixtures` en `/app/fixtures:ro` para los tests.

## Convenciones ya fijadas

- IDs integrados: impresoras `builtin-generic`, `builtin-inkjet`, `builtin-laser`, `builtin-borderless`;
  presets `builtin-*`.
- Errores HTTP: `PASSWORD_REQUIRED` y `DOCUMENT_NOT_READY` → 409, `FILE_TOO_LARGE` → 413, validación → 422,
  `RequestValidationError` → `INVALID_SETTINGS` con `errors[]`, rutas desconocidas → `NOT_FOUND`.
  Códigos nuevos: añádelos a `domain/errors.py` y al mapa de `api/errors.py`.
- Ajustes: `join=butt` fuerza `overlap_mm=0`; `info_band.position=none` fuerza alto 0; `final_size` exige
  exactamente uno de ancho/alto; al fusionar presets, un dict con clave `mode` se sustituye entero.
- Tolerancia del tamaño normalizado: máx(2 mm, 0.5 % del lado), por eje.
- ruff: reglas E, F, W, I, B, UP, SIM, RUF, C4, PT; línea 100. mypy estricto en `a0td.domain` con plugin de pydantic.
- Frontend: `npm run build` = `tsc --noEmit && vite build`. Textos solo vía i18n; claves nuevas en `es.json` **y** `en.json`.
- `localStorage` siempre dentro de try/catch (`frontend/src/lib/storage.ts`).

## E2E y smoke (WP-11)

```
docker compose -f compose.dev.yml --profile e2e run --rm e2e      # Playwright 1.55.0 contra app-full (red de compose)
bash scripts/container-smoke.sh                                     # REBUILD=0 para reutilizar imágenes
```

- Specs en `e2e/tests/` (helpers + a0-to-a4, password, raster, fit-sheet). Fijan `localStorage a0td.lang=en`.
- `data-testid` existentes: `generate`, `download`, `scale-mode-*`, `warning-<CODE>`, `password-input`,
  `password-invalid`, `job-failed`, `effective-dpi`. Helper compartido `downloadResult` (falla rápido si el
  trabajo falla).
- El papel por defecto depende de la región del navegador (WP-13). Playwright usa `locale: 'en-GB'` (A4 y mm,
  interfaz en inglés).
- La subida deduplica por SHA-256 (ING-10). `uploadFixture` (en `e2e/tests/helpers.ts`) añade bytes únicos a los
  fixtures `encrypted*`, para que cada ejecución sea independiente.
- Unidades: tamaños de página en **mm** (A4 apaisado = 841.89 × 595.28 pt); tolerancia ±0.05 mm en E2E. No los borres. Para los modales usa `getByRole('dialog', { name })`.
- Al registrar conversores nuevos (WP-12+), amplía las expectativas de `a0td formats` en `container-smoke.sh`.

## Docker (WP-03)

- Etapas: `frontend-build`, `libredwg-build`, `base`, `slim`, `full-tools`, `dev`, `full` (última = defecto).
- En la imagen: backend en `/app/backend`, SPA en `/app/static` (`A0TD_STATIC_DIR`), UID 10001, tini,
  `HOME=/tmp/home`, `TMPDIR=/data/tmp`. El entrypoint aplica `alembic upgrade head` si existe
  `/app/backend/alembic.ini`, y ejecuta cualquier otro comando que se le pase (p. ej. `a0td …`).
- Versiones en `full`: LibreOffice 25.2, Inkscape 1.4, Ghostscript 10.05, rsvg 2.60, qpdf 12.2, LibreDWG 0.13.3.
- **LibreOffice:** la plantilla de macros desactivadas está en `/usr/share/a0td/registrymodifications.xcu`.
  El conversor debe copiarla a `<perfil-del-trabajo>/user/registrymodifications.xcu` antes de lanzar
  `soffice -env:UserInstallation=file://<perfil>`; si no, las macros **no** quedan desactivadas.
- `A0TD_VERSION` llega como variable de entorno (build arg); la API debe leerla con prioridad sobre `version.py`.
- `.dockerignore` excluye `*.md` y `compose*.yml`.

## Fixtures (WP-05)

- `docker compose -f compose.dev.yml run --rm backend python ../tools/make_fixtures.py --out ../fixtures`
  (o el contenedor desechable). Deterministas (sha256 estable). `--large` añade `a0_300dpi.tif`.
- `fixtures/manifest.json` describe cada archivo (formato, páginas, size_mm, expectativas). Sus ids de formato
  se alinearán con los de `ingest/detect.py` (WP-06).
- `encrypted.pdf` contraseña `secret`; `encrypted_noprint.pdf` prohíbe imprimir; `corrupt.pdf` no abre.
- `fixtures/malicious/`: pixel bomb, 10 000 páginas, SVG XXE y href remoto, PS que lee `/etc/passwd`, PDF con JS,
  extensión engañosa, `.docm` con VBA falso.
- Los archivos > 200 KB están en `fixtures/.gitignore`: regenerarlos antes de testear si faltan.

## Trampas conocidas

- Windows + Git Bash: `MSYS_NO_PATHCONV=1` en comandos `docker run -v`/`-w`.
- No escribir el venv ni `node_modules` en el host (usar `UV_PROJECT_ENVIRONMENT=/tmp/venv` y el volumen `a0td-fe-nm`).
- `pyproject.toml` declara el script `a0td = a0td.cli.main:app`, que no existe hasta WP-09: no lo invoques antes.
- Comparaciones de nº de teselas: siempre `ceil_eps` (casos frontera exactos del Apéndice C).
- PyMuPDF: `page.transformation_matrix` **pierde el desplazamiento de la CropBox si la página está rotada**.
  Construye las matrices a mano desde `page.cropbox` y la altura de la MediaBox. Prueba siempre rotación +
  CropBox desplazada **combinadas**.
- Subprocesos: nada de `preexec_fn` (no es seguro con hilos). Límite de memoria con `prlimit --as=… --`
  delante del argv, y salida a archivos, no a memoria.
- No relajes una tolerancia geométrica para que pase un test. El objetivo es ≤ 0.05 mm (SPEC O2). Las cajas
  PDF admiten valores reales: si una librería redondea (p. ej. el backend PyMuPDF de ezdxf), corrige el
  tamaño de página después.
- `papers.oriented()` **siempre ordena** ancho y alto: no lo uses para rollos ni para nada cuyo ancho físico
  esté fijado.
- Cualquier URL o host que dé el usuario (IPP u otros) pasa la política SSRF: se resuelve, se fija la IP y se
  rechazan loopback, link-local y metadatos salvo los de la lista de permitidos. Los errores al cliente son
  genéricos.
- Rutas nuevas fuera de `/api/v1` (p. ej. `/metrics`): añádelas a las protegidas del `AuthMiddleware`.
- Si un límite rebaja la resolución (tope de píxeles, memoria), **propaga los PPP efectivos** a todo lo que
  venga después. Nunca etiquetes con los PPP pedidos. Los PPP de un raster salen de los metadatos de la
  ingesta, no de la etiqueta del TIFF.
- Cambios del canónico (p. ej. `set_physical_size` vectorial): construir en un temporal → ocupación → meta →
  `os.replace` del PDF al final, con vuelta atrás si algo falla. El factor se calcula desde el PDF en
  disco (idempotente). `IngestResult.save` es atómico.
- Saneado de SVG: se descarta cualquier CSS con `\`, las reglas `@` y los comentarios. Recorrido iterativo
  con profundidad máxima 256 y `data:` de 50 MB como máximo. No uses `ET.register_namespace`, porque es
  global al proceso.
- Cachés y salidas: nombre temporal único y `os.replace` atómico; nada de `.part` con nombre fijo.
- No mutar globals de librerías (`Image.MAX_IMAGE_PIXELS`) en cada llamada.
- Una variable de entorno vacía (`A0TD_X=""`) no es "sin valor": los campos `Path | None` de la config deben
  convertir `""` en `None` (caso real: `A0TD_STATIC_DIR=""` montaba la SPA en `/` y tapaba rutas).
- Los tests que añaden rutas a una app ya creada fallan si hay SPA montada en `/`: crear la app sin `static_dir`.
- **No reinicies Docker Desktop**: hay otros agentes con contenedores en marcha. Si Docker no responde,
  repórtalo al orquestador.
- Tests de SSE con TestClient: el stream solo vuelve al terminar; ábrelo en un hilo (ver `tests/api/test_hardening.py`).
  El render simulado para tests está en `tests/support/fake_render_engine.py` (modos `bigline`, `orphan`…).
- Los errores no controlados devuelven problem+json `INTERNAL_ERROR` sin detalles internos; el detalle completo va al log.
- El puerto 8000 del host lo ocupa otro proceso ajeno: no publiques el backend en 8000 del host; trabaja
  dentro de la red de compose (`http://backend:8000`) o usa un override local con otro puerto.
- La herramienta de subida de archivos de Playwright MCP no adjunta archivos: para smoke tests manuales, sube con curl.
- Debian trixie renombró paquetes con sufijo `t64` (p. ej. `libvips42t64`): verifica nombres con `apt-cache`.
