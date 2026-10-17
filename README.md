# a0-to-domestic

Convierte planos, carteles y patrones de gran formato (A0 y similares) en hojas listas para imprimir en una
impresora doméstica: a tamaño real o a escala en mosaico, o reducidos a una sola hoja. Todo se ejecuta en
local, dentro de Docker, y no envía nada a Internet.

> Estado: F1 (MVP), F2 y F3 implementadas según [SPEC.md](SPEC.md). Las desviaciones respecto a la
> especificación están en [docs/PROGRESS.md](docs/PROGRESS.md).

## Capturas (pendientes)

Marcadores a sustituir por capturas reales en `docs/img/`:

- [ ] `docs/img/01-inicio.png`: pantalla de inicio con la zona de subida y los presets rápidos.
- [ ] `docs/img/02-editor.png`: editor con la vista previa en rejilla y el panel de ajustes.
- [ ] `docs/img/03-mosaico.png`: vista de hoja con marcas, etiquetas y vecinos.
- [ ] `docs/img/04-resultado.png`: pantalla de resultado con descargas y botón de imprimir.
- [ ] `docs/img/05-plano-montaje.png`: plano de montaje con el cuadro de prueba.
- [ ] `docs/img/06-impresoras.png`: perfiles de impresora y asistente de calibración.

## Inicio rápido

Requisitos: Docker Desktop (Windows o macOS) o Docker Engine (Linux), en amd64 o arm64.

```bash
docker compose up -d
```

Abre **http://localhost:8080** en el navegador. La primera vez se construye la imagen `full`, que tarda varios
minutos. Para parar la aplicación: `docker compose down` (los datos se conservan en el volumen `a0data`).

## Imágenes

| Target | Tamaño aprox. | Contenido |
|---|---|---|
| `slim` | 968 MB | PDF, imágenes (PNG, JPEG, TIFF, HEIC/AVIF/JP2…), SVG (librsvg), PostScript/EPS/AI, XPS, DXF |
| `full` (por defecto) | 1.88 GB | Todo lo de `slim` más LibreOffice, Inkscape, LibreDWG y djvulibre: Office/ODF, DWG, HPGL, DjVu, CDR, EMF/WMF |

`compose.yml` construye `full`. Para construir la imagen ligera:

```bash
docker build -f docker/Dockerfile --target slim -t a0-to-domestic:slim .
```

## Funciones principales

- **Mosaico**: hojas en rejilla con solape de 3–50 mm o unión a tope con rombos de unión; orientación
  automática que minimiza el número de hojas; omisión de hojas vacías.
- **Escala**: 100 %, escala de dibujo (p. ej. 1:50 → 1:100), tamaño final, copia en una sola hoja,
  rejilla fija (p. ej. 2 × 2 A3) o máximo de hojas.
- **Montaje**: líneas de corte, marcas de alineación, etiquetas con los vecinos, regla de 50 mm, plano de
  montaje y cuadro de prueba de 50 mm para comprobar la escala antes de imprimir todo.
- **Selección**: región (un detalle), recorte automático de márgenes blancos, rotación, elección de hojas
  concretas para reimprimir una sola.
- **Color y salida**: color, escala de grises, blanco y negro con umbral, invertir, blanquear escaneos,
  rasterizar la salida, ZIP de imágenes por hoja.
- **Análisis**: capas PDF (OCG), avisos de legibilidad (texto y líneas finas tras escalar), escala de
  dibujo detectada en el texto, estimación de tinta.
- **Impresoras**: perfiles con márgenes y calibración en tres pasos; presets integrados y de usuario;
  historial de documentos y trabajos.
- **Impresión**: desde el visor del navegador o directa por IPP (F3); carpeta vigilada para procesos por lotes.
- **CLI**: el mismo motor desde la línea de comandos (`a0td`).
- **Idiomas**: español (por defecto) e inglés.

## Formatos admitidos

Se detecta el tipo por contenido, no por extensión.

| Familia | Extensiones | Imagen |
|---|---|---|
| PDF (incluidos PDF/A, PDF/X, cifrados) | pdf | slim |
| Imágenes | png, jpg/jpeg, tif/tiff (multipágina), bmp, gif, webp | slim |
| Imágenes modernas | heic/heif, avif, jp2/j2k | slim |
| SVG | svg, svgz | slim (rsvg); full (Inkscape) |
| PostScript / Illustrator | ps, eps, ai | slim |
| XPS | xps, oxps | slim |
| DXF | dxf | slim |
| Office y ODF | docx, doc, xlsx, xls, pptx, ppt, odt, ods, odp, odg, rtf, vsd, vsdx, pub | full |
| Metarchivos | emf, wmf | full |
| DWG | dwg | full |
| HPGL / plóter | plt, hpgl, hpg | full |
| DjVu | djvu, djv | full |
| CorelDRAW | cdr | full (fidelidad variable) |

No admitidos: documentos con macros (p. ej. `.docm`) y cualquier formato no listado. Un formato que existe
pero no está en la imagen `slim` devuelve `FORMAT_NOT_AVAILABLE_IN_IMAGE`; usa `full`.

Para listar los conversores disponibles en la imagen en uso: `docker run --rm a0-to-domestic a0td formats`.

## Línea de comandos

```bash
docker run --rm -v "$PWD:/work" a0-to-domestic a0td tile /work/plano.pdf --paper A4
docker run --rm -v "$PWD:/work" a0-to-domestic a0td info /work/plano.pdf
docker run --rm a0-to-domestic a0td formats
```

En PowerShell, sustituye `"$PWD"` por `"${PWD}"`. Subcomandos: `formats`, `info`, `plan`, `tile`, `fit`,
`calibration-page`. Casi todos aceptan `--json`. Los ajustes se pasan con flags, con `--preset` o con
`--settings job.json`. Si no se indica `-o`, la salida se escribe junto al archivo de entrada. Los errores
salen por stderr como `error [CODIGO]: …` con código de salida 1.

## Configuración

Todas las variables de entorno (prefijo `A0TD_`) están descritas en [SPEC.md §12.4](SPEC.md#124-variables-de-entorno).
Se fijan en `compose.yml`, bajo `environment:`.

## Seguridad

- **Solo local por defecto:** `compose.yml` publica el puerto en `127.0.0.1:8080`.
- **Acceso desde la red local (LAN):** quita `127.0.0.1` del puerto y activa la autenticación:
  `A0TD_AUTH_MODE=password` con `A0TD_PASSWORD_HASH` (hash Argon2). Si la autenticación está mal configurada,
  la aplicación no arranca. No la expongas a Internet.
- **Sin salida a Internet:** se recomienda ejecutar el contenedor sin acceso a Internet, porque LibreOffice no
  bloquea por sí mismo los recursos remotos enlazados en documentos OOXML (ver SPEC §14). Una red Docker con
  `internal: true` cumple ese objetivo, pero la impresión IPP necesita llegar a la impresora en la LAN.
- **Contenedor endurecido:** usuario no root, sistema de archivos de solo lectura, `no-new-privileges`,
  `cap_drop: ALL` y límites de CPU y memoria.
- **Archivos subidos:** se tratan como no confiables. Los PDF de salida no contienen JavaScript, acciones,
  adjuntos ni anotaciones del original.
- **Privacidad:** sin telemetría ni llamadas externas. Los permisos de impresión de los PDF se respetan.

## Licencia

El proyecto se distribuye bajo **AGPL-3.0-or-later** (Copyright 2026 AdrianY1997; texto completo en [LICENSE](LICENSE)),
decisión D1 de [SPEC.md §18](SPEC.md#18-decisiones-adoptadas-por-defecto), porque depende de PyMuPDF y Ghostscript
(AGPL-3.0), y de LibreDWG e Inkscape (GPL).
Las licencias de los componentes de terceros están en [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## Documentación

- [docs/user-guide.md](docs/user-guide.md): guía de uso paso a paso (en español, para usuarios).
- [docs/development.md](docs/development.md): arquitectura, entorno de desarrollo, pruebas y CI.
- [docs/adr/](docs/adr/): decisiones de arquitectura.
- [SPEC.md](SPEC.md): especificación funcional y técnica.
- [docs/AGENTS.md](docs/AGENTS.md) y [docs/PROGRESS.md](docs/PROGRESS.md): manual para agentes y estado de la implementación.
