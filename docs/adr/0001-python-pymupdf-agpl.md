# 0001. Python 3.13 con PyMuPDF y licencia AGPL-3.0

- Estado: aceptada
- Fecha: 2026-10-15 (SPEC v0.1, D1 y D2)

## Contexto

El núcleo necesita imposición vectorial (reutilizar la página origen como XObject recortado por tesela), render,
análisis de cajas y capas, y lectura de PDF, XPS, SVG e imágenes. El ecosistema de render e imagen de Python es el
más completo para ello. Node o Java tienen bibliotecas más pobres para esta tarea.

## Decisión

Backend en Python 3.13 con FastAPI y PyMuPDF como motor PDF. El proyecto se distribuye bajo **AGPL-3.0**, licencia
compatible con PyMuPDF y Ghostscript.

## Alternativas consideradas

- **pypdfium2 + pikepdf**: licencia permisiva, pero sin la misma capacidad de imposición y de análisis.
  Queda documentada como alternativa en SPEC §18 (D1).
- **Java (PDFBox) o Node (pdf-lib + sharp)**: ecosistema más pobre para render y conversión (D2).

## Consecuencias

- Distribuir la imagen o un fork implica publicar el código bajo AGPL-3.0.
- Ghostscript (AGPL-3.0) y LibreDWG/Inkscape (GPL) imponen obligaciones similares. Se listan en
  `THIRD_PARTY_LICENSES.md`.
- Un cambio a licencia permisiva requeriría sustituir PyMuPDF y reescribir la imposición.
