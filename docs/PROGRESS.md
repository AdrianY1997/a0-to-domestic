# Progreso de la implementación

Lo mantiene el orquestador tras cada paquete de trabajo (WP). Spec: [SPEC.md](../SPEC.md). Manual de
agentes: [AGENTS.md](AGENTS.md).

Leyenda: ✅ hecho · 🔄 en curso · ⏳ pendiente · 🔍 auditoría

## Estado

| WP | Contenido | Estado | Modelo / effort | Resultado |
|---|---|---|---|---|
| WP-01 | Esqueleto backend + dominio | ✅ | Sonnet / medium | 84 tests, cobertura ~98 %, ruff y mypy limpios |
| WP-02 | Planificador | ✅ 🔍 | Sonnet / high | 189 tests (89 unit + 14 Hypothesis…), cobertura dominio 99 % |
| WP-03 | Docker (slim, full, dev, compose, devcontainer) | ✅ | Sonnet / medium | 3 targets construyen; `full` endurecida OK; slim 968 MB, full 1.88 GB, dev 2.81 GB |
| WP-04 | Esqueleto frontend | ✅ | Sonnet / medium | 13 tests, lint y build OK |
| WP-05 | Generador de fixtures | ✅ | Sonnet / medium | 16 fixtures + 8 maliciosos, deterministas |
| WP-06 | Ingesta F1 | ✅ 🔍 | Sonnet / high | 346 tests en total (153 nuevos de integración); fixtures y maliciosos según manifiesto |
| WP-07 | Render F1 | ✅ 🔍 | Sonnet / high | 74 tests nuevos (vector, marcas, raster, golden, salidas); CA-1/2/3/5 y reensamblado §15.4 en verde |
| WP-08 | Trabajos, almacenamiento y API F1 | ✅ 🔍 | Sonnet / high | 51 tests de API nuevos (ingesta real, render simulado); todos los endpoints F1 |
| WP-09 | CLI | ✅ | Sonnet / low | 10 tests; `formats`, `info`, `plan`, `tile`, `fit`, `calibration-page` (sale con 2 hasta F2); auditoría conjunta con WP-11 |
| WP-10 | Frontend F1 | ✅ 🔍 | Sonnet / high | 59 tests, lint y build OK (chunk máx. 337 kB); smoke manual completo contra el stack dev |
| WP-11 | E2E y tests de contenedor | ✅ 🔍 | Sonnet / medium | 4/4 E2E (Chromium, ~28 s) contra `full` endurecida; `scripts/container-smoke.sh` PASS |
| WP-12 | Conversores F2 | ✅ 🔍 | Sonnet / medium | 7 conversores (office, svg, postscript, xps, heif/avif/jp2…); 507 tests unit+integration; smoke con JSON y disponibilidad por target |
| WP-13 | Perfiles y calibración | ✅ 🔍 | Sonnet / medium | CRUD de impresoras, PDF y API de calibración, CLI `calibration-page`, asistente de 3 pasos, PAP-05; frontend 114 tests (verificado por el orquestador) |
| WP-14 | Mosaico F2 (UI) | ✅ | Sonnet / medium | 91 tests frontend; selección de hojas, arrastre de rejilla, región, recorte, final_size/drawing_scale, alternativas y ahorro. Auditoría conjunta con WP-13/16 |
| WP-15 | Color y salidas F2 | ✅ 🔍 | Sonnet / medium | ~30 tests nuevos; grises (gs), B/N, invertir, blanquear, rasterizar, ZIP, copia reducida, cuadro de prueba en primera hoja |
| WP-16 | Presets, historial, inglés, auth | ✅ 🔍 | Sonnet / medium | Backend 306 tests (api+unit); frontend 121 tests; auth none/password/token, presets CRUD + import/export, ajustes, historial, deduplicación |
| WP-17 | CAD y otros formatos | ✅ 🔍 | Sonnet / high | Backend 812 tests (4 fallos de aislamiento en `test_printing`, asignados al arreglo de WP-19); frontend 143; smoke PASS. DXF, DWG, HPGL, DjVu, CDR; diálogo CAD; flags `--cad-*` en la CLI |
| WP-18 | Análisis avanzado | ✅ 🔍 | Sonnet / medium | Backend 839 tests, frontend 146: capas OCG (también al imprimir), legibilidad, escala detectada, espejo, capa de marcas, grosor mínimo, tinta |
| WP-19 | IPP, carpeta vigilada, banner, métricas | ✅ 🔍 | Sonnet / medium | Backend 804 tests (los 6 fallos son de WP-17 en curso); frontend 136; cliente IPP propio en Python, carpeta vigilada, rollo, apaisado rotado, `/metrics`, CI multi-arch |
| WP-20 | Documentación final | ✅ | Haiku / medium | README, `docs/user-guide.md`, `docs/development.md`, `docs/adr/0001-0009`, `THIRD_PARTY_LICENSES.md`. Falta `LICENSE` (decisión del usuario, D1) |

Auditorías:
- WP-01 + WP-04 ([informe](../.recall/audits/2026-10-15T00-00-wp01-wp04.md)): 2🔴 2🟡. ✅ Corregido (Haiku / low; 84 tests backend y 13 frontend en verde):
  - Ruta SPA con barras invertidas en Windows.
  - URL de `gen:api` (la correcta es `/api/openapi.json`).
  - Tema Mantine.
  - Los tipos de API generados quedan para WP-08/WP-10.
- WP-02 ([informe](../.recall/audits/2026-10-15T12-00-wp02-planner.md)): 0🔴 1🟡 1❓. ✅ Corregido (Haiku / low). También corregido `A0TD_STATIC_DIR=""` (montaba la SPA en `/`); backend 193 tests en verde:
  - `GridOccupancy.any_in` con rectángulos a la izquierda del origen.
  - En orientación automática, si fallan ambas orientaciones se propaga el error; si falla una, se usa la otra (decisión del orquestador).
- WP-06 ([informe](../.recall/audits/2026-10-15T00-00-wp06-ingest.md)): 1🔴 4🟡 1🔵 1❓. ✅ Corregido (Sonnet / medium; 338 tests unit+integration en verde):
  - 🔴 `/Rotate` + CropBox desplazada sacaba el contenido de la página.
  - 🟡 `preexec_fn` no es seguro con hilos → se usa `prlimit`.
  - 🟡 Salida de herramientas sin tope.
  - 🟡 Ruta relativa en qpdf.
  - 🟡 `MAX_IMAGE_PIXELS` global.
  - 🔵 PPP anisótropos.
  - ❓ Bombas dentro de un PDF: quedan acotadas por el subproceso de trabajo de WP-08 (timeout + RLIMIT_AS).
- WP-07 ([informe](../.recall/audits/2026-10-16T12-00-wp07-render-engine.md)): 0🔴 2🟡 2🔵 1❓. ✅ Corregido (Sonnet / low; 426 tests, ruff y mypy limpios, golden sin cambios):
  - Etiquetas visibles con solape < 6 mm. Decisión del orquestador: ID compacto dentro de la franja receptora; spec §5.9 actualizada.
  - "1/N" en hojas omitidas en la vista previa.
  - Tests que faltaban: PDF hostil, corrección de impresora en el plano de montaje, sentido de rotación.
- WP-09 + WP-10 ([informe](../.recall/audits/2026-10-16T12-00-wp09-wp10-cli-frontend.md)): 0🔴 6🟡 1🔵 1❓.
  - CLI ✅ corregido (Sonnet / low; 17 tests): `-o` opcional (junto a la entrada), flags extra (fit-sheet, final-size, drawing-scale, align, order, naming), validación de `--settings`, tests.
  - Frontend ✅ corregido (Sonnet / low; 61 tests, lint y build OK):
    - `clipPath` de la imagen.
    - Transformación tomada del plan, no de los ajustes, durante el debounce.
    - `<html lang>` desde el inicio.
    - Clave `INTERNAL_ERROR`.
    - ❓ Avisos ignorados: decisión del orquestador, se guardan por documento en el store persistido.
- WP-18 ([informe](../.recall/audits/2026-10-17T12-00-wp18-layers-legibility.md)): **2🔴** 4🟡 1🔵 2❓. ✅ Corregido (Sonnet / medium; backend 578 tests unit+render+api, frontend 151). gs conserva los OCG pero renumera los xref y descarta el contenido de las capas apagadas, por lo que en grises cualquier cambio de capas rasteriza (`LAYERS_RASTERIZED`). Las OCMD se normalizan según la especificación, porque MuPDF las evalúa mal. Decisiones del orquestador:
  - Las capas apagadas en el origen siguen apagadas por defecto; el campo nuevo `source.visible_layers` permite encenderlas. Panel, vista previa y salida deben coincidir.
  - En grises, las capas se identifican por nombre, nunca por xref de la copia de gs. Si gs elimina las capas, se rasteriza con el aviso `LAYERS_RASTERIZED`.
  - La capa de marcas se excluye de la ocultación.
  - Las estadísticas se acotan antes de materializar y el grosor se mide con la CTM aplicada.
  - OCMD AllOff/AnyOff.
  - Interruptor "Rasterizar salida" en la UI.
- WP-17 ([informe](../.recall/audits/2026-10-16T12-00-wp17-cad-hpgl-djvu.md)): **2🔴** 4🟡 1🔵 1❓. ✅ Corregido (Sonnet / medium; 563 tests integration+api+cli). El redondeo del tamaño de página de ezdxf se corrige reescribiendo el MediaBox/CropBox exacto y una matriz `cm`; tolerancia 0.05 mm y test de una línea de 1000 mm a 1:50 = 20.00 mm:
  - 🔴 Deduplicación que devuelve un CAD reprocesado con otras opciones. Decisión del orquestador: sin deduplicación para dxf/dwg.
  - 🔴 `cad-options` destruye el canónico antes de terminar. Ahora se genera en un directorio temporal y se sustituye solo si termina bien.
  - Validación de unidades.
  - Marco HPGL.
  - Límites de ezdxf.
  - PPP de DjVu.
  - ❓ Decisión del orquestador: CAD sin unidades emite también `PHYSICAL_SIZE_ASSUMED` (`reason: cad_units`).
- WP-19 ([informe](../.recall/audits/2026-10-16T12-00-wp19-ipp-roll-hotfolder.md)): **4🔴** 4🟡 1🔵. ✅ Corregido (Sonnet / medium; unit, property y api en verde, mypy limpio). Nuevo `printing/netpolicy.py`. El aislamiento de `test_printing` está pendiente de confirmar en la suite completa:
  - 🔴 Hojas de rollo giradas.
  - 🔴 División por cero en `_shrink_roll`.
  - 🔴 SSRF en `ipp_uri`.
  - 🔴 `/metrics` sin autenticación.
  - Carpeta vigilada: reprocesado infinito y TOCTOU.
  - IPP: lectura sin tope, TLS sin verificar y PDF entero en memoria.
  - Política SSRF del orquestador: se permite la LAN privada y se rechazan loopback, link-local, metadatos y multicast, salvo `A0TD_IPP_ALLOW_HOSTS`. La IP resuelta se fija al conectar y los errores al cliente son genéricos.
- WP-16 ([informe](../.recall/audits/2026-10-16T12-00-wp16-auth-presets-history.md)): 0🔴 4🟡 2🔵 1❓ + tests que faltan. ✅ Corregido (Sonnet / medium; backend 322 tests api+unit, frontend 122). Nueva columna `documents.was_encrypted` (migración 0002), `A0TD_TRUSTED_PROXIES`, docs de API protegidos y `schema.d.ts` regenerado. Antes, verificación del orquestador: **backend completo 675 tests en verde**, ruff, formato y mypy limpios. Hallazgos:
  - Clave secreta vacía o truncada aceptada.
  - Límite de intentos detrás de un proxy.
  - Deduplicación que devuelve un PDF descifrado. Decisión del orquestador: solo se deduplican documentos nunca cifrados.
  - `/api/docs` abiertos con autenticación activa.
  - Diccionario de intentos sin lock.
  - Logout sin CSRF.
  - ❓ Decisión del orquestador: el modo token es solo para API y CLI, y la UI lo avisa.
- WP-13 + WP-14 ([informe](../.recall/audits/2026-10-16T12-00-wp13-wp14.md)): 0🔴 4🟡 1❓. ✅ Corregido (Sonnet / low; backend `test_printers` 9 tests, frontend lint, tests y build OK):
  - Doble clic que alterna y además abre la vista previa.
  - Nominales de calibración sin validar en el servidor.
  - Guardar la calibración sin medir.
  - PUT de impresora que borra los valores por defecto.
  - ❓ Decisión del orquestador: aplicar una sugerencia de ahorro aplica también su orientación.
- WP-12 ([informe](../.recall/audits/2026-10-16T12-00-wp12-f2-converters.md)): 0🔴 4🟡 3🔵 1❓. ✅ Corregido (Sonnet / medium; 237 tests de ingesta). Las claves de LibreOffice para OLE y carga remota quedan para el endurecimiento de red de F3:
  - Escapes CSS que saltan el saneado de SVG.
  - División por cero con viewBox diminuto.
  - `set_physical_size` vectorial no atómico.
  - Reescalado que no cubre todas las cajas.
  - Enlaces externos de LibreOffice → "nunca actualizar".
  - Recursión, tamaño de `data:` y `register_namespace` global.
  - ❓ Decisión del orquestador: un SVG en `px` cuyo tamaño a 96 px/in coincide con un tamaño normalizado se considera físico.
- WP-15 (sin archivo de informe): 1🔴 5🟡 3❓. ✅ Corregido (Sonnet / medium; 31 tests de color y salidas). La colocación nunca estuvo desplazada: el error era la etiqueta del TIFF. Nuevo aviso `ZIP_DPI_REDUCED` (tope de ZIP 150 MP):
  - 🔴 PPP efectivos tras el tope de píxeles no propagados (geometría −18 % a 600 PPP en A0).
  - Memoria al rasterizar (por bandas).
  - Caché de gris con nombre fijo e invalidación por mtime.
  - Respaldo de 1.0 ppm.
  - ZIP no atómico.
  - Decisiones del orquestador: nombres únicos en el ZIP (`P{n}-`), 1 bit se mantiene, paleta → sRGB, y la ruta rasterizada debe reensamblar igual que la vectorial.
- WP-11 (sin archivo de informe; resumen aquí): 0🔴 6🟡 2🔵 1❓.
  - E2E ✅ corregido (Sonnet / low; 5/5): CA-1 comprueba páginas de 297×210 mm ±0.05 y el tamaño del PDF; CA-2 comprueba contenido de 200×282.8 centrado (plan del trabajo); CA-4 comprueba 120.8 PPP (testid `effective-dpi`) y el raster asumido de 840.7×1189 mm; nuevo testid `job-failed`; `retries` solo en CI.
  - Smoke: parsear el JSON y comprobar disponibilidad por target. Encargado a WP-12.
  - ❓ CA-3 y CA-5 sin E2E: decisión del orquestador, quedan cubiertos por tests de integración del backend.
- WP-08 ([informe](../.recall/audits/2026-10-15T00-00-wp08-jobs-storage-api.md)): 0🔴 6🟡 3🔵 1❓. ✅ Corregido (Sonnet / medium). Verificación del orquestador: **backend completo 504 tests en verde**, ruff, formato y mypy (`domain`, `render`) limpios:
  - SSE colgado si se borra un trabajo en curso.
  - stdout sin drenar tras una línea gigante.
  - Purga que puede borrar subidas en curso.
  - Trabajo bloqueante en el event loop.
  - Caché de miniaturas no atómica.
  - Reintentos de contraseña concurrentes.
  - Recuperación sin limpiar parciales.
  - Detalles de excepción filtrados al cliente.
  - Errores 500 sin cabeceras de seguridad.
  - ❓ `killpg` tras recoger el líder: el orquestador decide matar el grupo antes de recogerlo.

## Desviaciones y decisiones respecto a la spec

| WP | Decisión | Motivo |
|---|---|---|
| WP-01 | Nueva variable `A0TD_STATIC_DIR` (carpeta de la SPA) | La spec no definía cómo localizar la SPA |
| WP-01 | `join=butt` fuerza `overlap_mm=0` en vez de rechazar | Más tolerante con presets y la UI |
| WP-01 | `/api/v1/info` añade `auth_mode`, `default_locale`, `ipp_enabled` | Lo necesita la UI |
| WP-01 | `/readyz` siempre "ready" | Hasta que exista BD (WP-08) |
| WP-04 | `@types/node` añadido; `build` incluye `tsc --noEmit` | Necesario para `vite.config.ts` / tipado estricto |
| WP-04 | Carpetas `features/` y store de zustand aún no creadas | Se crean en WP-10 |
| WP-02 | SPEC C.5 tenía un error: falta 91.56 % → 16 hojas (vertical 4×4) | Corregido en la spec |
| WP-02 | Flag `exact_scale` en `plan()` para ocultar ahorro | El planificador no conoce presets; lo decide quien llama |
| WP-02 | Ahorro solo en modo `percent` | En los demás modos la escala no es libre |
| WP-02 | Nuevo aviso `BOX_FALLBACK` | Añadido a la spec §10 |
| WP-02 | `counts.blank` = vacías seleccionadas; IDs desconocidos en `only_sheets` se ignoran; `P2-A1` y `A1` válidos en pág. 2 | Spec no lo concretaba |
| WP-02 | Tope interno de 20 000 celdas de rejilla → `TOO_MANY_SHEETS` | Evitar rejillas enormes con escalas absurdas |
| WP-02 | `Plan.content_origin_mm` extra | Lo usa el render |
| WP-02 | `mirror` y `landscape_as_rotated_portrait` sin efecto en el plan | Se tratan en render (WP-07) / F3 |
| WP-06 | Índices de página base 0 en archivos (`page-0.tif`, `pages/0/`) | Coherente con `Page.index` |
| WP-06 | 3 mapas de ocupación precalculados (strict/normal/tolerant) | Cambiar sensibilidad sin re-renderizar |
| WP-06 | Despeckle = quitar píxeles aislados (no apertura morfológica) | La apertura borraba líneas de 1 px |
| WP-06 | Ocupación < 2 px/mm en páginas enormes (tope 16 Mceldas) | Memoria |
| WP-06 | `ai` aún no se procesa (F2, WP-12) | Fuera de F1 |
| WP-06 | mypy no limpio en `ingest/` | Solo se exige estricto en `domain/` y `render/` |
| WP-07 | `render_job` añade kwargs `doc_name`, `locale`, `now` | Nombre de salida, idioma de textos y fecha reproducible en tests |
| WP-07 | Sin implementar aún (avisos `RenderWarning`): espejo, modos de color ≠ color, rasterizar, ZIP, `test_square=first_sheet` | F2/F3 (WP-15, WP-18) |
| WP-07 | La corrección de impresora se aplica también al plano de montaje | Que el cuadro de prueba refleje la calibración |
| WP-07 | Un plano de montaje por página origen; `test_sheet_only` solo la primera página | Montajes independientes por página |
| WP-07 | Nada que imprimir (todo vacío o deseleccionado) → `INVALID_SETTINGS` | Evitar PDFs vacíos |
| WP-07 | Flechas de vecinos dibujadas como triángulos | Helvetica no tiene glifos de flecha |
| WP-07 | mypy estricto también en `a0td.render.*` (imports de pymupdf/pyvips sin tipos) | Spec §13 |
| WP-18 | La tinta se guarda en `options_json` y se expone como `JobOut.ink` | Sin migración |
| WP-18 | Rasterizado y vista previa reabren el PDF con las capas apagadas | MuPDF lee la configuración de capas solo al abrir |
| WP-18 | Falta en la UI el interruptor "rasterizar salida" (SAL-04); el grosor mínimo depende de él | Pendiente (auditoría) |
| WP-18 | Ediciones mínimas en archivos de otros agentes (planner, worker, manager, schemas, ingest) | Integración |
| WP-17 | Conversores `dxf` y `dwg` separados (no uno `cad`) | Disponibilidad propia en cada imagen (dxf también en slim) |
| WP-17 | El DWG de fixture se convierte con un segundo intento solo de entidades (con aviso) | El round-trip de `dxf2dwg` pierde información |
| WP-17 | Sin xvfb en la imagen; ODA solo funciona si `xvfb-run` ya está instalado | ODA lo aporta el usuario |
| WP-17 | DXF sin unidades → mm, marcado como "asumido" en el diálogo CAD | No pasa por el diálogo de tamaño físico |
| WP-17 | Subir con opciones CAD salta la deduplicación | Mismo archivo con distintas opciones = distinto canónico |
| WP-17 | HPGL: se ejecuta la extensión de Inkscape con un marco calculado; no dibuja textos (aviso) | El importador recortaba a 8×10 in |
| WP-19 | IPP con codificador propio en Python (sin `ipptool`) | Sin dependencias del sistema; probado con un servidor IPP falso |
| WP-19 | Rollo como `target.roll_mm {width, max_length}`, no como `PaperId` | Ancho fijo y largo variable |
| WP-19 | `/metrics` fuera de `/api/v1` y sin auth. Decisión del orquestador: exigir auth/token si `AUTH_MODE != none` | Pendiente de la auditoría |
| WP-19 | Nuevos códigos `PRINT_REJECTED`, `IPP_DISABLED`, `DISCOVERY_UNAVAILABLE`; deps `prometheus-client`, `zeroconf` | Reconstruir `a0td:dev` |
| WP-16 | Sesiones con cookie firmada sin estado (12 h); logout solo borra la cookie | Simplicidad (revisar en auditoría) |
| WP-16 | Nuevos códigos `PRESET_READONLY`, `INVALID_CREDENTIALS`, `RATE_LIMITED`, `CSRF_INVALID` | ADM-03 |
| WP-16 | Autenticación mal configurada → la app no arranca | Fallo seguro |
| WP-16 | Descripción del preset dentro de `settings_json`; retención editable salvo si la fija el entorno | Sin cambios de esquema |
| WP-16 | `schema.d.ts` no regenerado; tipos nuevos escritos a mano | Pendiente: `npm run gen:api` |
| WP-13 | Sin migración: `borderless` y papeles admitidos en claves reservadas de `defaults_json` | Evitar cambio de esquema (revisar en auditoría) |
| WP-13 | Impresoras integradas no se calibran: la UI ofrece duplicarlas | Las integradas son de solo lectura (403 `PRINTER_READONLY`) |
| WP-13 | Nuevos códigos `PRINTER_READONLY`, `CALIBRATION_OUT_OF_RANGE` | CAL-02 |
| WP-13 | `/plan/sheet-preview` ahora pasa el perfil de impresora | Faltaba la corrección en la vista previa |
| WP-13 | Flag `--locale` en `calibration-page` | Textos es/en |
| WP-12 | SVG sin unidades → se escala a A0 (`ingest/vector_size.py`, sidecar `native-size.json`), `PHYSICAL_SIZE_ASSUMED`; `set_physical_size` también vale para páginas vectoriales | ANA-03 para SVG |
| WP-12 | JP2 por el conversor heif; EPS/PS/XPS de fixtures escritos a mano | Simplicidad y determinismo |
| WP-12 | `FONTS_SUBSTITUTED` aproximado (fuentes citadas vs `fc-match`) | Best effort |
| WP-12 | LibreOffice no bloquea enlaces remotos en OOXML | Riesgo añadido a SPEC §14; mitigación en F3 (sin salida de red) |
| WP-12 | cdr/vsdx/pub dependen de los filtros de LibreOffice; no probados | F3 (WP-17) |
| WP-15 | Espejo (ESC-06) sin implementar → aviso `MIRROR_NOT_IMPLEMENTED` | Queda para F3 |
| WP-15 | Golden `raster_scan-p0.png` regenerado | COL-05: vecino más cercano en 1 bit (cambia ~2 % de píxeles de la miniatura) |
| WP-15 | Cuadro de prueba en primera hoja: arriba-izquierda sobre fondo blanco; se omite si no cabe | Spec no fijaba la posición |
| WP-15 | Sin `pdf` en `formats` no se escribe PDF; `rasterize` no hace nada en documentos raster; ZIP renderizado desde el PDF final | Coherencia |
| WP-14 | Rango "B3-B5" = bloque entre las dos esquinas (fila si están en la misma fila) | Generaliza a rejilla 2D |
| WP-14 | Espacio sobre una hoja la alterna; Enter abre la vista previa | Clic = alternar |
| WP-14 | Doble clic alterna dos veces (efecto neto nulo) antes de abrir la vista previa | Aceptado; revisar en la auditoría |
| WP-14 | Alternativas solo se piden con el panel abierto | Coste del cálculo |
| WP-10 | Botón "documento de ejemplo" oculto | No hay endpoint de ejemplo en el backend (pendiente F2) |
| WP-10 | Subida con XHR | `fetch` no da progreso de subida |
| WP-10 | `gen:api` vía `scripts/gen-api.mjs` con `API_URL` (en compose: `http://backend:8000`) | Funciona dentro de la red de compose |
| WP-10 | El editor envía `preset_id` y lo borra al editar la escala | `exact_scale` correcto |
| WP-10 | Sin incluir aún: alternativas y ahorro, región, excluir con clic | F2 (WP-14) |
| WP-08 | Endpoint extra `POST /storage/purge` | Botón "Borrar ahora" (PRE-04) |
| WP-08 | Especificación del trabajo al worker por stdin | La contraseña del PDF nunca toca disco |
| WP-08 | Contraseña incorrecta → 202 + `PASSWORD_INVALID` en el documento | Flujo asíncrono coherente |
| WP-08 | `preset_id` opcional en `/plan` y `/jobs` | Para decidir `exact_scale` |
| WP-08 | Montaje `./fixtures` en `compose.dev.yml` | Los tests con fixtures se saltaban en el contenedor |
| WP-03 | `slim` = 968 MB (objetivo 600 MB) | venv 272 MB + libvips/gs/pymupdf; se acepta, revisar en WP-19 |
| WP-03 | Etapa extra `full-tools` compartida por `dev` y `full` | Evita duplicar la instalación de herramientas |
| WP-05 | Ids de formato propios en `manifest.json` | Se alinean con `detect.py` en WP-06 |
| WP-05 | PDFs cifrados con un cifrador RC4 propio con /ID fijo | PyMuPDF aleatoriza /ID y rompe el determinismo |

## Pendientes conocidos

- Aviso de Vite: chunk de 526 kB (dividir con lazy routes en WP-10).
- `gen:api` del frontend no ejecutado (requiere backend levantado; WP-08/WP-10).
- **WP-12:** copiar la plantilla de macros de LibreOffice al perfil de cada trabajo (ver AGENTS.md › Docker).
- **WP-08:** `/api/v1/info` debe reportar `A0TD_VERSION` (env) en vez de solo `version.py`.
- **WP-11:** fijar la etiqueta real de la imagen de Playwright (ahora es un placeholder) y probar `compose.dev.yml up`.

## Siguiente

**✅ Implementación completa (WP-01 a WP-20). Verificación final del orquestador (2026-10-17):**

| Suite | Resultado |
|---|---|
| Backend (pytest completo) | **922 passed**; ruff, formato y mypy (`domain`, `render`) limpios |
| Frontend | lint OK, **151 tests**, build OK |
| Smoke de contenedores (slim, full, dev reconstruidas) | **PASS** |
| E2E (Playwright contra `full` endurecida) | **5/5** |

El aislamiento de `test_printing` queda confirmado en la suite completa.

Pendientes:

- Archivo `LICENSE` (decisión del usuario; la spec adopta AGPL-3.0, D1).
- Prueba física de impresión (§15.8).
- Capturas de la guía de usuario.
- `slim` supera el objetivo de tamaño.
- Endurecimiento de red de los conversores (sin salida a Internet).

**Verificación de F2 (orquestador):**

- Backend completo: 675 tests ✅.
- Smoke de contenedores: PASS ✅.
- E2E: 5/5 ✅ tras corregir los tests. No había fallos de la app:
  - Locale de Playwright `en-US` → `en-GB`: el papel por región daba Carta.
  - Los fixtures cifrados se hacen únicos por ejecución para no chocar con la deduplicación por SHA-256.
  - Dos ejecuciones seguidas en verde.

**F2 completa y verificada.**

**F1 (MVP) completo**:

- Backend: 504 tests.
- Frontend: 59 tests.
- E2E: 4/4.
- Smoke de contenedores: PASS.

En curso: correcciones del frontend (auditoría de WP-10) y auditoría de WP-11.
Después: F2, con WP-12 a WP-16.
