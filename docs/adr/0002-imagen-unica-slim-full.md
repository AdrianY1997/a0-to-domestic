# 0002. Una sola imagen Docker con targets slim y full

- Estado: aceptada, con desviación de tamaño documentada
- Fecha: 2026-10-15 (SPEC §12.1, D3); WP-03 y WP-17

## Contexto

Hay dos necesidades opuestas: una imagen pequeña para los formatos comunes y una imagen completa para Office,
DWG, HPGL, DjVu y CDR, que exigen LibreOffice e Inkscape (cientos de megabytes). Una sola imagen grande penaliza
la descarga; separar en servicios distintos complica el despliegue doméstico (O5: un `docker compose up`).

## Decisión

Un único Dockerfile con etapas compartidas y dos targets publicados:

- **`slim`**: PDF, raster, HEIC/AVIF/JP2, SVG (rsvg), PS/EPS/AI, XPS y DXF (ezdxf).
- **`full`** (por defecto en `compose.yml`): `slim` más LibreOffice, Inkscape, LibreDWG y djvulibre.

Las etapas `dev` y `full` comparten `full-tools`, para no instalar las herramientas dos veces.

`a0td formats` indica qué conversores están disponibles en la imagen en uso. Un formato sin conversor devuelve
`FORMAT_NOT_AVAILABLE_IN_IMAGE`.

## Alternativas consideradas

- **Conversores en un servicio aparte con red interna**: reservado para el caso de exposición a Internet (D3).
  Se descarta por ahora porque complica el despliegue local.

## Consecuencias

- Tamaños medidos: `slim` 968 MB (objetivo inicial 600 MB), `full` 1.88 GB, `dev` 2.81 GB. El objetivo de `slim`
  no se cumple: el venv ocupa 272 MB y libvips, Ghostscript y PyMuPDF el resto. Se acepta; se revisará.
- DXF está en `slim` porque ezdxf es Python puro. DWG está en `full` porque depende de LibreDWG.
- Hay que probar cada conversor en la imagen que lo contiene (`skip` si el binario no existe).
