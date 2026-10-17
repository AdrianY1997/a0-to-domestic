# Licencias de terceros

Lista orientativa de los componentes principales que usa a0-to-domestic, con su licencia. Las versiones
marcadas con **(verificar)** no están confirmadas y deben comprobarse contra la imagen construida antes de
cada distribución. Esta lista no es asesoramiento legal.

## Componentes del sistema (imagen Docker)

| Componente | Versión | Licencia | Uso en el proyecto |
|---|---|---|---|
| Ghostscript | 10.05 | AGPL-3.0 | PostScript/EPS/AI; escala de grises vectorial |
| LibreOffice | 25.2 | MPL-2.0 | Conversión de Office, ODF, EMF/WMF, VSD, PUB, CDR (`full`) |
| Inkscape | 1.4 | GPL-2.0-or-later | SVG de alta fidelidad y HPGL (`full`) |
| librsvg (`rsvg-convert`) | 2.60 | LGPL-2.1-or-later | SVG por defecto (`slim`) |
| LibreDWG (`dwg2dxf`) | 0.13.3 | GPL-3.0 | DWG a DXF (`full`) |
| djvulibre (`ddjvu`) | verificar | GPL-2.0 | DjVu a TIFF (`full`) |
| libvips | verificar | LGPL-2.1-or-later | Procesado de imágenes grandes |
| qpdf | 12.2 | Apache-2.0 | Reparación de PDF de respaldo |
| Fuentes Carlito, Caladea, Liberation, DejaVu, Noto | verificar | OFL / licencias propias de cada fuente | Sustitutos métricos de Office y texto de los planos |
| Playwright (solo desarrollo y E2E) | 1.55.0 | Apache-2.0 | Pruebas de extremo a extremo |

## Dependencias Python (backend)

| Paquete | Licencia | Uso |
|---|---|---|
| PyMuPDF | AGPL-3.0 (también comercial) | Lectura, render e imposición de PDF |
| ezdxf | MIT | DXF |
| FastAPI | MIT | API HTTP |
| Pydantic / pydantic-settings | MIT | Modelos y configuración |
| SQLAlchemy | MIT | Acceso a SQLite |
| Alembic | MIT | Migraciones de base de datos |
| Uvicorn | BSD-3-Clause | Servidor HTTP |
| Typer | MIT | CLI |
| pyvips | MIT (envoltorio de libvips) | Procesado de raster |
| Pillow | MIT-CMU (HPND) | Imágenes |
| pillow-heif | BSD-3-Clause (dependencias de libheif: verificar LGPL) | HEIC/AVIF |
| numpy | BSD-3-Clause | Análisis de ocupación |
| defusedxml | PSF-2.0 | Análisis seguro de XML (SVG) |
| argon2-cffi | MIT | Hash de contraseña |
| prometheus-client | Apache-2.0 | Métricas `/metrics` |
| zeroconf | LGPL-2.1-or-later | Descubrimiento mDNS de impresoras |

## Dependencias JavaScript (frontend)

| Paquete | Licencia | Uso |
|---|---|---|
| React / React DOM | MIT | Interfaz |
| Mantine | MIT | Componentes de interfaz |
| TanStack Query | MIT | Estado de servidor |
| Zustand | MIT | Estado del editor |
| react-i18next / i18next | MIT | Traducciones |
| react-router | MIT | Rutas |
| react-zoom-pan-pinch | MIT | Zoom y paneo de la vista previa |
| openapi-typescript | MIT | Tipos generados desde OpenAPI |
| Vite, Vitest, ESLint, Prettier | MIT | Herramientas de desarrollo |

## Licencias de copia

Los textos completos de AGPL-3.0, GPL-2.0, GPL-3.0, LGPL-2.1, MPL-2.0, Apache-2.0, BSD-3-Clause y MIT deben
distribuirse junto con la imagen o enlazarse desde ella. Consulta la SBOM (syft, SPDX) que genera la CI.
