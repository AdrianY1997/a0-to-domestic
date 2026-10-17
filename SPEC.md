# a0-to-domestic — Especificación

> Convierte documentos de gran formato (A0 y similares), en casi cualquier formato, en hojas listas para
> imprimir en una impresora doméstica: en mosaico a tamaño real o a escala, o reducidos a una sola hoja.
> Todo local, empaquetado en Docker.

| | |
|---|---|
| Versión | 0.1 — base para implementación |
| Fecha | 2026-10-15 |
| Nombre en código | `a0-to-domestic` · paquete Python `a0td` · CLI `a0td` · prefijo de entorno `A0TD_` |
| Notación | Números con punto decimal. Dimensiones de papel en mm, `ancho × alto` en orientación vertical. |

## Índice

1. [Visión y objetivos](#1-visión-y-objetivos)
2. [Usuarios y casos de uso](#2-usuarios-y-casos-de-uso)
3. [Alcance y supuestos](#3-alcance-y-supuestos)
4. [Glosario](#4-glosario)
5. [Modelo geométrico (núcleo)](#5-modelo-geométrico-núcleo)
6. [Requisitos funcionales](#6-requisitos-funcionales)
7. [Formatos de entrada y normalización](#7-formatos-de-entrada-y-normalización)
8. [Interfaz de usuario](#8-interfaz-de-usuario)
9. [Arquitectura](#9-arquitectura)
10. [API HTTP](#10-api-http)
11. [Modelo de datos](#11-modelo-de-datos)
12. [Docker y entornos](#12-docker-y-entornos)
13. [Requisitos no funcionales](#13-requisitos-no-funcionales)
14. [Seguridad](#14-seguridad)
15. [Estrategia de pruebas](#15-estrategia-de-pruebas)
16. [Plan de implementación](#16-plan-de-implementación)
17. [Riesgos](#17-riesgos)
18. [Decisiones (adoptadas por defecto)](#18-decisiones-adoptadas-por-defecto)
- [Apéndice A — Catálogo de papeles](#apéndice-a--catálogo-de-papeles)
- [Apéndice B — JobSettings](#apéndice-b--jobsettings)
- [Apéndice C — Vectores de prueba del planificador](#apéndice-c--vectores-de-prueba-del-planificador)
- [Apéndice D — Textos de montaje e impresión](#apéndice-d--textos-de-montaje-e-impresión)
- [Apéndice E — Firmas de detección de formato](#apéndice-e--firmas-de-detección-de-formato)

---

## 1. Visión y objetivos

Un A0 (841 × 1189 mm, 1 m²) normalmente exige un plóter o una copistería. La mayoría de la gente solo
necesita una de dos cosas:

- **Copia a tamaño real o a escala** para trabajar sobre ella (patrones de costura, plantillas de
  carpintería, planos para replantear, pósteres): se imprime en mosaico de hojas que se recortan y pegan.
- **Copia reducida** para leer o revisar: el documento entero en una sola A4/A3/Carta.

La app hace ambas cosas con una impresora doméstica: **escala exacta**, **mínimo de hojas**, **montaje
guiado** (marcas, etiquetas, plano de montaje) y **privacidad** (nada sale de la máquina).

| Objetivo | Métrica |
|---|---|
| O1 Rapidez de uso | Del archivo al PDF listo en ≤ 3 interacciones usando un preset |
| O2 Precisión | Error geométrico en el PDF ≤ 0.05 mm; error del montaje físico ≤ 0.5 % con impresora calibrada |
| O3 Ahorro | Nº mínimo de hojas para la configuración dada (orientación automática, hojas vacías omitidas) |
| O4 Privacidad | Cero llamadas externas; funciona sin Internet |
| O5 Despliegue | Un `docker compose up` |

## 2. Usuarios y casos de uso

| Persona | Documento típico | Necesidad clave |
|---|---|---|
| Arquitectura / ingeniería | Plano A0/A1 en PDF, DWG o DXF | Escala exacta (1:50 → 1:100), revisión en A3, imprimir solo una zona |
| Costura, patronaje, manualidades | Patrón A0 de copistería en PDF con capas por talla | 1:1 exacto, cuadro de prueba, ocultar tallas, unir hojas |
| Póster / divulgación | Póster A0 en PDF o PPTX | Borrador en A3/A4, mosaico decorativo |
| Uso doméstico | Mapa, lámina, foto grande (JPG/HEIC) | Mural por hojas, ahorro de tinta |

Casos de uso:

- **CU-1** Imprimir un A0 a tamaño real en A4 (o Carta) y montarlo.
- **CU-2** Imprimir un A0 reducido en una sola hoja.
- **CU-3** Imprimir a una escala de dibujo (1:50 → 1:100) en varias hojas.
- **CU-4** Imprimir solo una región (un detalle) a tamaño real.
- **CU-5** Encajar un póster en una rejilla fija (p. ej. 2 × 2 A3).
- **CU-6** Reimprimir una hoja concreta (atasco, mancha).
- **CU-7** Calibrar la impresora (márgenes y escala) una vez y reutilizar el perfil.
- **CU-8** Procesar por lotes con la CLI o una carpeta vigilada.

## 3. Alcance y supuestos

**Dentro del alcance:** ingesta de formatos (§7), análisis del documento, planificación del mosaico,
generación de PDF/imágenes con marcas y plano de montaje, perfiles y calibración de impresora, presets,
historial, CLI, impresión directa por IPP (fase 3), todo en una imagen Docker.

**Fuera del alcance:** editar el contenido del documento, OCR, plóters, drivers USB, app móvil nativa,
multiusuario con cuentas, servicio público en Internet. Futuro (no planificado): modo inverso (unir un
patrón A4 en un A0), PSD, DWF.

**Supuestos:**

- Autoalojada, un usuario o un hogar/LAN. Docker Desktop (Windows/macOS) o Docker Engine (Linux), amd64 y arm64.
- Se imprime desde el visor PDF del navegador/sistema; la impresión directa es opcional.
- Interfaz en español por defecto, inglés como segundo idioma.

## 4. Glosario

| Término | Definición |
|---|---|
| Documento origen | Archivo subido por el usuario. Puede tener varias páginas. |
| Documento canónico | Versión normalizada del origen: PDF (vectorial) o raster teselado, con tamaño físico conocido. |
| Espacio de vista | Página canónica tras aplicar caja (crop/trim…) y rotación; mm, origen arriba-izquierda, y hacia abajo. |
| Papel / hoja | Papel físico de la impresora (A4…) / cada página del PDF generado. |
| Márgenes físicos | Bandas del papel donde la impresora no puede imprimir. |
| Área útil | Parte imprimible de la hoja que recibe contenido del origen. |
| Tesela | Porción del origen que se imprime en una hoja. |
| Solape | Franja de contenido repetida en hojas vecinas para pegarlas. |
| Paso | Distancia entre el inicio de dos teselas consecutivas (área útil − solape). |
| Escala `s` | mm en papel por mm de origen (1.0 = tamaño real). |
| Plano de montaje | Página extra con la miniatura, la rejilla y las instrucciones. |
| Perfil de impresora | Márgenes reales, papeles admitidos y correcciones de escala de una impresora concreta. |
| PPP efectivos | Resolución de un raster a la escala de impresión elegida. |

## 5. Modelo geométrico (núcleo)

El planificador (`a0td.domain.planner`) es una **función pura** sin E/S: recibe la geometría de la página y
los ajustes y devuelve un `Plan`. Es la única fuente de verdad: la UI, la CLI y el render lo consumen.

### 5.1 Unidades y coordenadas

- Dominio en **mm** (`float`). El PDF trabaja en pt (1 pt = 25.4/72 mm); la conversión solo ocurre en el render.
- Origen arriba-izquierda, eje y hacia abajo (igual que PyMuPDF).
- Redondeo hacia arriba tolerante: `ceil_eps(x) = ceil(x - 1e-9)`. Obligatorio en todo cálculo de nº de
  teselas (ver casos frontera en el Apéndice C).
- Todas las coordenadas del `Plan` se expresan en el espacio de vista.

### 5.2 Geometría del origen

Orden de transformaciones:

1. Página canónica (sin `/Rotate`; ver §7.3).
2. Caja: `crop` (por defecto), `trim`, `bleed` o `media`. Si existe TrimBox se sugiere en la UI.
3. Rotación del usuario: 0/90/180/270 (sentido horario).
4. Región opcional `R` (rectángulo en espacio de vista). Sin región, `R` = página entera.
5. Recorte automático opcional: `R` se reduce al rectángulo envolvente del contenido no blanco
   (mapa de ocupación, §9.6) más 2 mm de seguridad.

Resultado: tamaño de origen `S = (S_w, S_h)` en mm y su desplazamiento `R.x0, R.y0`.

### 5.3 Hoja

- Papel `P = (P_w, P_h)` ya orientado: vertical `P_w ≤ P_h`, horizontal al revés.
- Márgenes físicos `m = (top, right, bottom, left)` definidos con el papel **en vertical, tal como entra
  en la impresora**. Si la hoja es horizontal, el visor o el driver la girará 90° en un sentido que no
  conocemos, así que se usan márgenes seguros:
  - `top' = bottom' = max(left, right)`
  - `left' = right' = max(top, bottom)`
  - Opción `landscape_as_rotated_portrait` (fase 3): generar páginas verticales con el contenido girado
    dentro y usar los márgenes exactos.
- Banda de información opcional de alto `b` (arriba o abajo, por defecto ninguna).
- **Área útil** `C`: `C_w = P_w − m_l − m_r`, `C_h = P_h − m_t − m_b − b`.
- Validación: `C_w, C_h ≥ 30 mm`; con unión por solape, `3 mm ≤ o ≤ min(C_w, C_h) − 20 mm`.

### 5.4 Escala

| Modo | Parámetros | `s` |
|---|---|---|
| `percent` | `p` | `p / 100` |
| `drawing_scale` | 1:`a` → 1:`b` | `a / b` (1:50 → 1:100 ⇒ 0.5) |
| `final_size` | `width_mm` **o** `height_mm` | `width_mm / S_w` o `height_mm / S_h` |
| `fit_sheet` | — | `min(C_w / S_w, C_h / S_h)` |
| `fit_grid` | `cols`, `rows` | `min((cols·d_w + o) / S_w, (rows·d_h + o) / S_h)` |
| `fit_count` | `max_sheets` N | máximo de `fit_grid(c, r)` sobre todos los `c·r ≤ N` y ambas orientaciones; empate → menos hojas |

Límites: `0.01 ≤ s ≤ 10`. En los modos `fit_*` la orientación automática elige la que da **mayor** `s`
(empate → menos hojas → vertical).

### 5.5 Rejilla

```
T   = S · s                              tamaño impreso del origen (mm de papel)
d   = C − o                              paso
n_c = max(1, ceil_eps((T_w − o) / d_w))  columnas
n_r = max(1, ceil_eps((T_h − o) / d_h))  filas
K   = n · d + o                          cobertura de la rejilla
h   = K − T  (≥ 0)                       holgura por eje
```

- Origen de la rejilla `g` por eje (mm de papel, relativo al borde de `T`, siempre `≤ 0`):
  `start → 0`, `center → −h/2` (por defecto), `end → −h`. Se suma el desplazamiento del usuario `δ` y se
  acota a `g ∈ [−h, 0]`. El plan devuelve el rango válido de `δ` para que la UI lo arrastre.
- Tesela `(r, c)` en mm de papel relativos a `T`: `x = g_x + c·d_w`, `y = g_y + r·d_h`, tamaño `C`.
- En espacio de vista: `src = (R.x0 + x/s, R.y0 + y/s, C_w/s, C_h/s)`. Lo que cae fuera de `R` se deja en blanco.
- Colocación en la hoja: el rectángulo útil empieza en `(m_l, m_t + b_top)`. La tesela se dibuja recortada
  (clip) a ese rectángulo.
- **Corrección de impresora** (perfil calibrado, `k = medido / nominal` por eje): se aplica como una
  transformación afín global de la hoja, escala `1/k_x, 1/k_y` alrededor del centro de la página, a todo
  lo dibujado (contenido y marcas).

### 5.6 Orientación automática

Para `percent`, `drawing_scale` y `final_size` se evalúan las dos orientaciones y se elige la de **menos
hojas impresas** (sin contar vacías si `skip_blank` está activo y hay mapa de ocupación). Empate → menor
cobertura `K_w·K_h` → vertical.

### 5.7 Nomenclatura y orden

- Filas con letras (A…Z, AA, AB…), columnas con números desde 1. ID = fila + columna: `B3`.
  Alternativas: `number_number` (`2-3`) y `sequential` (`7`).
- Orden de impresión: por filas (por defecto) o por columnas. "Hoja k de N" cuenta solo hojas impresas.
- Varias páginas: prefijo `P2-` en los IDs cuando hay más de una página.

### 5.8 Métodos de unión

**Superposición (`overlap`, por defecto).** Cada junta comparte una franja de `o` mm. Por defecto la hoja
de la derecha va encima de la de la izquierda y la de abajo encima de la de arriba. Para la hoja `(r, c)`:

- Bordes **de corte** (va encima del vecino): izquierdo si `c > 0`, superior si `r > 0`. Línea de corte
  discontinua justo en el borde del área útil; el usuario recorta el margen blanco por ahí.
- Bordes **receptores** (el vecino se pega encima): derecho si `c < n_c−1`, inferior si `r < n_r−1`.
  Franja de solape `[C.x1 − o, C.x1]` (o `[C.y1 − o, C.y1]`), con línea de alineación en `C.x1 − o`, que
  es donde debe caer el borde recortado del vecino, y triángulos ▶ en sus extremos.

```
        A1 (recibe)                          A2 (va encima)
 ┌─────────────────────────────┐      ┌─────────────────────────────┐
 │ ┌───────────────────────┬──┐│      │ ┌┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┐ │
 │ │                       ┃▒▒││      │ ┆← línea de corte          ┆ │
 │ │       contenido       ┃▒▒││  ⇒   │ ┆  (recortar el margen)    ┆ │
 │ │                       ┃▒▒││      │ ┆                          ┆ │
 │ └───────────────────────┴──┘│      │ └┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┘ │
 └─────────────────────────────┘      └─────────────────────────────┘
   ┃ línea de alineación   ▒ franja de solape (o mm): pegar aquí; queda oculta tras el montaje
```

**A tope (`butt`).** `o = 0`. Todos los bordes interiores son de corte. Se dibujan medios rombos de unión
en el punto medio de cada borde interior, que forman un rombo completo al juntar las hojas (convención de
patronaje). Van etiquetados con la junta (`A1|A2`).

### 5.9 Marcas, etiquetas y regla

Todas las marcas se dibujan **dentro del área imprimible**. En unión por superposición no deben quedar
visibles tras el montaje.

| Elemento | Posición | Por defecto |
|---|---|---|
| Línea de corte | Bordes de corte, sobre el borde del área útil; discontinua 3 mm / 2 mm | sí |
| Línea de corte exterior | Bordes sin vecino (para un borde final limpio) | no |
| Alineación | Bordes receptores: línea en `x1 − o` / `y1 − o` y triángulos en los extremos | sí |
| Rombos de unión | Solo `butt`: punto medio de cada borde interior | sí |
| Etiqueta | ID grande + "Hoja k/N" + vecinos (`↑A3 →B4 ↓C3 ←B2`) + nombre corto del documento | sí |
| Regla de 50 mm | En la franja receptora, junto a la etiqueta | sí |
| Sombreado de pegado | Relleno gris 10 % en las franjas receptoras | no (ahorro de tinta) |

Colocación de la etiqueta y la regla: franja receptora inferior → si no hay, la derecha (texto girado 90°)
→ si no hay ninguna (última hoja), esquina inferior derecha del área útil en cuerpo 6 pt, o en la banda de
información si existe. Si `o < 6 mm`, la etiqueta se reduce al ID en cuerpo 5 pt (sin vecinos ni regla)
y se mantiene dentro de la franja receptora, oculta tras el montaje. Solo las hojas sin franja receptora
la llevan en la esquina.
Color de las marcas `#808080`, grosor 0.5 pt, ambos configurables.

### 5.10 Plano de montaje

Página adicional (al principio por defecto, al final o ninguna) del mismo papel que el mosaico:

- Miniatura vectorial de la región con la rejilla, los IDs y las hojas omitidas tachadas.
- Resumen: documento, papel y orientación, escala (% y 1:N si procede), rejilla, hojas impresas, vacías y
  omitidas, tamaño final, unión y solape, perfil de impresora, fecha y versión de la app.
- Cuadro de prueba de 50 × 50 mm y de 2 × 2 in, y regla de 100 mm. Es la forma de verificar la escala antes
  de imprimir el resto.
- Instrucciones de impresión y montaje (Apéndice D).

### 5.11 Sugerencias de ahorro y alternativas

- **Alternativas:** para la escala actual, nº de hojas con cada papel admitido por el perfil y cada orientación.
- **Ahorro:** escalas umbral por debajo de la actual (hasta el 80 % de ella) donde baja el nº de hojas:
  `s = (n·d + o) / S` por eje y orientación. Se devuelven los pares (escala, hojas) Pareto-óptimos, máximo 3.
  Ejemplo: A0 en A4 horizontal al 100 % = 21 hojas; al 96.72 % = 18. Solo se muestran si el modo no es de
  escala exacta (`percent` 100 con un preset de patrón o plano las oculta).

## 6. Requisitos funcionales

Fases: **F1** = MVP, **F2**, **F3** (ver §16).

### 6.1 Ingesta (ING)

| ID | Requisito | Fase |
|---|---|---|
| ING-01 | Subida por arrastrar y soltar o selector, varios archivos a la vez (un documento cada uno), con barra de progreso. Límite `A0TD_MAX_UPLOAD_MB`. | F1 |
| ING-02 | Detección del tipo por contenido (Apéndice E), no por extensión. Formato no soportado → error claro con la lista de formatos. | F1 |
| ING-03 | Normalización al documento canónico (§7.3) en un trabajo en segundo plano, con progreso. | F1 |
| ING-04 | PDF con contraseña de apertura: pedirla y reintentar. Si los permisos prohíben imprimir, informar y no procesar (no se eluden restricciones). | F1 |
| ING-05 | Multipágina: lista de páginas con tamaño y selección (`all`, `1,3-5`). | F1 |
| ING-06 | Reparación automática de PDF dañados (MuPDF; qpdf como respaldo), con aviso. | F1 |
| ING-07 | Aplanar anotaciones y campos de formulario (sellos, marcas de Bluebeam…) en el canónico, con aviso. | F1 |
| ING-08 | Formatos de fase 2 y 3 según §7.1. Formato no disponible en la imagen `slim` → error `FORMAT_NOT_AVAILABLE_IN_IMAGE`. | F2/F3 |
| ING-09 | Pegar imagen desde el portapapeles. | F2 |
| ING-10 | Reutilizar un documento ya ingerido si coincide el SHA-256 (no reconvertir). | F2 |
| ING-11 | Fuentes de usuario montadas en `/data/fonts` disponibles para LibreOffice e Inkscape. | F2 |

### 6.2 Análisis (ANA)

| ID | Requisito | Fase |
|---|---|---|
| ANA-01 | Tamaño físico por página (mm), cajas (crop/trim/bleed/media) y tamaño normalizado más cercano (Apéndice A; tolerancia ±2 mm o 0.5 %), p. ej. "A0 vertical". | F1 |
| ANA-02 | Clasificación de página: `vector`, `raster` (una imagen cubre ≥ 90 % y apenas hay vectores ni texto) o `mixed`. | F1 |
| ANA-03 | Raster: píxeles y PPP de los metadatos. Sin PPP, o con PPP inverosímiles (lado físico > 5 m o < 50 mm), se marca `PHYSICAL_SIZE_ASSUMED` y se asume A0 en la orientación de la imagen. El usuario puede fijar el tamaño por papel, ancho, alto o PPP. Lo mismo para SVG sin unidades físicas. | F1 |
| ANA-04 | PPP efectivos a la escala elegida: < 150 → aviso `LOW_DPI`, < 100 → `VERY_LOW_DPI`. | F1 |
| ANA-05 | Mapa de ocupación (§9.6) para detectar hojas vacías y para el recorte automático. | F1 |
| ANA-06 | Fondo oscuro (luminancia media < 30 %) → sugerir invertir. Fondo no blanco en escaneos → sugerir blanquear. | F2 |
| ANA-07 | Capas PDF (OCG): lista con su estado; ocultar o mostrar en la salida. | F3 |
| ANA-08 | Legibilidad: cuerpo de texto mínimo y percentil 10 tras escalar (spans de PyMuPDF); aviso `SMALL_TEXT` si < 5 pt. Líneas más finas que 0.1 mm tras escalar → `THIN_LINES`. | F3 |
| ANA-09 | Detectar en el texto la escala del dibujo ("1:50", "E 1/100") y proponerla en el modo `drawing_scale`. | F3 |

### 6.3 Papel e impresoras (PAP)

| ID | Requisito | Fase |
|---|---|---|
| PAP-01 | Catálogo de papeles (Apéndice A) y tamaño personalizado (50–500 × 50–1500 mm). | F1 |
| PAP-02 | Orientación: automática (§5.6), vertical u horizontal. | F1 |
| PAP-03 | Márgenes físicos por lado, editables. Perfil por defecto "Genérica (5 mm)". | F1 |
| PAP-04 | Perfiles de impresora (CRUD): nombre, tipo, papeles admitidos, márgenes por papel, sin bordes, color o mono, corrección de escala `k_x, k_y`, URI IPP y valores por defecto. Integrados: Genérica 5 mm, Inyección típica (3/3/5/3), Láser típica (4.5), Sin bordes (0). | F2 |
| PAP-05 | Papel y unidades por defecto según la región del navegador: Carta e in en US/CA; Carta y mm en MX, CO, VE, CL, PE…; A4 y mm en el resto. Configurable. | F2 |
| PAP-06 | Rollo o banner: ancho fijo y largo máximo; teselado en tiras. | F3 |
| PAP-07 | Leer las capacidades de la impresora por IPP (`media-col-database`, márgenes) para autocompletar el perfil. | F3 |

### 6.4 Escala y región (ESC)

| ID | Requisito | Fase |
|---|---|---|
| ESC-01 | Modos `percent` (por defecto 100), `fit_sheet`, `fit_grid` y `fit_count` (§5.4). | F1 |
| ESC-02 | Modos `final_size` y `drawing_scale`. La UI muestra la escala resultante y 1:N. | F1 (backend) / F2 (UI) |
| ESC-03 | Rotación 0/90/180/270. | F1 |
| ESC-04 | Región: dibujarla sobre la vista previa o escribirla numéricamente. | F2 |
| ESC-05 | Recorte automático de márgenes blancos del documento (§5.2, paso 5). | F2 |
| ESC-06 | Espejo horizontal (transferencias, serigrafía). | F3 |

### 6.5 Mosaico (MOS)

| ID | Requisito | Fase |
|---|---|---|
| MOS-01 | Cálculo de la rejilla según §5.5, con nº de hojas, rejilla, tamaño final y holgura. | F1 |
| MOS-02 | Unión `overlap` (solape 3–50 mm, por defecto 10) o `butt`. | F1 |
| MOS-03 | Alineación de la rejilla por eje (start, center o end) y desplazamiento arrastrando en la vista previa, acotado. | F1 (alineación) / F2 (arrastre) |
| MOS-04 | Omitir hojas vacías (por defecto sí, con sensibilidad strict/normal/tolerant); el plano de montaje las marca. | F1 |
| MOS-05 | Seleccionar a mano qué hojas imprimir: clic en la vista previa o lista "A1, B3-B5". Sirve para reimprimir. | F2 |
| MOS-06 | Orden por filas o columnas; nomenclatura (§5.7). | F1 |
| MOS-07 | Panel de alternativas y sugerencias de ahorro (§5.11). | F2 |
| MOS-08 | Tope de hojas por trabajo `A0TD_MAX_SHEETS` (por defecto 500) → error `TOO_MANY_SHEETS`. | F1 |

### 6.6 Marcas y montaje (MAR)

| ID | Requisito | Fase |
|---|---|---|
| MAR-01 | Líneas de corte, marcas de alineación, etiquetas, vecinos y regla según §5.9. | F1 |
| MAR-02 | Rombos de unión en modo `butt`. | F1 |
| MAR-03 | Plano de montaje según §5.10, con cuadro de prueba. | F1 |
| MAR-04 | Sombreado de pegado, banda de información, línea de corte exterior y cuadro de prueba en la primera hoja. | F2 |
| MAR-05 | Marcas en una capa PDF (OCG) "Marcas" que se puede ocultar en el visor. | F3 |

### 6.7 Color y calidad (COL)

| ID | Requisito | Fase |
|---|---|---|
| COL-01 | Transparencia sobre blanco; ICC y CMYK → sRGB; 16 bits → 8 bits. | F1 |
| COL-02 | Escala de grises que conserva el vectorial (Ghostscript `pdfwrite`). | F2 |
| COL-03 | Blanco y negro con umbral ajustable (0–255, por defecto 160). Con umbral alto equivale a "todo lo no blanco a negro", útil para planos en color. Rasteriza. | F2 |
| COL-04 | Invertir y blanquear fondo (niveles automáticos y despeckle) en escaneos. Rasteriza. | F2 |
| COL-05 | Interpolación al reescalar raster: automática (Lanczos para tono continuo, vecino más cercano para dibujo lineal de 1 bit). | F2 |
| COL-06 | Grosor mínimo de línea (solo en salida rasterizada; `set_graphics_min_line_width` de MuPDF). | F3 |
| COL-07 | Estimación de cobertura de tinta por hoja y total. | F3 |

### 6.8 Salida (SAL)

| ID | Requisito | Fase |
|---|---|---|
| SAL-01 | PDF multipágina. Cada página mide exactamente el papel. Origen vectorial → salida vectorial: la página origen es un Form XObject compartido, recortado por tesela, así que el tamaño **no** se multiplica por el nº de hojas. Origen raster → cada hoja embebe **solo su recorte**, sin reescalar al alza, a como mucho `max_dpi` (por defecto 600). | F1 |
| SAL-02 | `ViewerPreferences`: `PrintScaling /None`, `Duplex /Simplex`, `PickTrayByPDFSize true`. Metadatos (título, creador, versión). Ajustes del trabajo adjuntos como `a0td-job.json`. | F1 |
| SAL-03 | Plantilla de nombre de archivo `{name}_{paper}-{orientation}_{cols}x{rows}_{scale}pct` (nombre saneado: ASCII, `[A-Za-z0-9_-]`, ≤ 60 caracteres). | F1 |
| SAL-04 | Rasterizar la salida a 150/200/300/600 PPP, para impresoras o drivers que se atascan con PDFs complejos. | F2 |
| SAL-05 | ZIP de imágenes por hoja (PNG, JPEG o TIFF), con nombres por ID. | F2 |
| SAL-06 | Copia reducida (`fit_sheet`) generada en el mismo trabajo además del mosaico. | F2 |
| SAL-07 | Compresión adecuada al contenido: JPEG q90 para tono continuo, Flate para dibujo lineal y gris, 1 bit para blanco y negro. | F1 |

### 6.9 Impresión (IMP)

| ID | Requisito | Fase |
|---|---|---|
| IMP-01 | Botón "Imprimir": abre el PDF en el visor del navegador y muestra las instrucciones (Apéndice D). | F1 |
| IMP-02 | "Imprimir primero la hoja de prueba": genera solo el plano de montaje con el cuadro de prueba. | F1 |
| IMP-03 | Impresión directa por IPP / IPP Everywhere / AirPrint a una URI configurada: `print-scaling=none`, `media` (nombre PWG), `sides=one-sided`, modo de color, copias y rango de hojas. Si la impresora no acepta PDF, PWG Raster con Ghostscript. Estado del trabajo. | F3 |
| IMP-04 | Descubrimiento mDNS (`_ipp._tcp`). Solo funciona con red `host` en Linux; en Docker Desktop se introduce la URI a mano. | F3 |

### 6.10 Calibración (CAL)

| ID | Requisito | Fase |
|---|---|---|
| CAL-01 | Página de calibración para el papel elegido: reglas horizontal y vertical tan largas como permita el papel (papel − 30 mm, múltiplo de 10), cuadros de 50 mm y 2 in, y marcos concéntricos cada 0.5 mm entre 1 y 10 mm del borde (etiquetados cada mm) para leer los márgenes reales por lado. | F2 |
| CAL-02 | Asistente: imprimir → medir → introducir longitudes y márgenes → guardar en el perfil `k = medido / nominal` y márgenes medidos + 0.5 mm. Si `k ∉ [0.97, 1.03]`, avisar de que probablemente se imprimió con "Ajustar a página" y no guardar. | F2 |
| CAL-03 | Aplicar la corrección al generar (§5.5) e indicarlo en el plano de montaje. | F2 |

### 6.11 Presets, historial y ajustes (PRE)

| ID | Requisito | Fase |
|---|---|---|
| PRE-01 | Presets integrados: "Tamaño real en A4", "Tamaño real en Carta", "Tamaño real en A3", "Reducido a una A4", "Reducido a una A3", "Póster 2×2 A3", "Patrón de costura 1:1 (A4, a tope)". | F1 |
| PRE-02 | Presets de usuario: crear desde los ajustes actuales, editar, borrar, exportar e importar JSON. | F2 |
| PRE-03 | Historial de documentos y trabajos con reabrir, regenerar y descargar mientras no caduquen. | F2 |
| PRE-04 | Retención: borrado automático tras `A0TD_RETENTION_HOURS` (por defecto 72) y botón "Borrar ahora". | F1 |
| PRE-05 | Ajustes de la app: idioma, unidades, perfil y papel por defecto, retención (solo lectura si la fija el entorno). | F2 |

### 6.12 CLI y automatización (CLI)

| ID | Requisito | Fase |
|---|---|---|
| CLI-01 | `a0td info <archivo>`, `a0td plan`, `a0td tile`, `a0td fit`, `a0td calibration-page`, `a0td formats`. Mismas opciones que JobSettings mediante flags o `--settings job.json`. Se ejecuta en la imagen: `docker run --rm -v "$PWD:/work" a0-to-domestic a0td tile /work/plano.pdf --paper A4`. | F1 |
| CLI-02 | Carpeta vigilada: `/data/inbox/<preset>/archivo` → `/data/outbox/<preset>/`, con originales en `.done/` o `.error/` (y log). Por **sondeo**, porque inotify no cruza bind mounts de Docker Desktop en Windows. | F3 |

### 6.13 Administración (ADM)

| ID | Requisito | Fase |
|---|---|---|
| ADM-01 | Configuración por variables de entorno (§12.4). | F1 |
| ADM-02 | `/healthz` (proceso vivo), `/readyz` (BD y almacenamiento listos) y `/api/v1/info` (versión, conversores disponibles, límites). | F1 |
| ADM-03 | Autenticación opcional `A0TD_AUTH_MODE=password` (cookie de sesión) o `token` (Bearer para API y CLI). | F2 |
| ADM-04 | Métricas Prometheus opcionales en `/metrics`. | F3 |

### 6.14 Criterios de aceptación clave

- **CA-1 (CU-1).** Dado un PDF vectorial A0 vertical, con A4, 100 %, solape 10 mm, márgenes 5 mm y
  orientación automática, se generan 21 hojas A4 horizontales (rejilla 3 × 7) más el plano de montaje. Cada
  página mide 297 × 210 mm ± 0.01 pt. El PDF ocupa ≤ 1.2 × el origen + 200 KB. La prueba de reensamblado
  (§15.4) pasa.
- **CA-2 (CU-2).** Mismo origen con `fit_sheet` en A4 → 1 página vertical, escala 23.78 %, contenido de
  200.0 × 282.8 mm centrado.
- **CA-3 (CU-3).** Plano A0 a 1:50 con `drawing_scale` 50 → 100 en A4 → 8 hojas horizontales (2 × 4); el
  plano de montaje indica "1:100".
- **CA-4 (raster).** JPEG de 4000 × 5657 px sin PPP → se asume A0, PPP efectivos 120.8 y aviso `LOW_DPI`;
  al fijar `fit_sheet` en A4 desaparece el aviso.
- **CA-5 (vacías).** Un A0 con contenido solo en la mitad superior y `skip_blank` → las hojas sin
  contenido no se imprimen, el plano de montaje las tacha y "Hoja k/N" cuenta solo las impresas.
- **CA-6 (contraseña).** PDF cifrado → estado `password_required`; contraseña correcta → `ready`;
  incorrecta → `PASSWORD_INVALID` sin bloquear el documento.
- **CA-7 (seguridad).** Las muestras maliciosas de §15.6 se rechazan o neutralizan sin colgar el
  servicio, sin escribir fuera del directorio del trabajo y sin acceso a red.

## 7. Formatos de entrada y normalización

### 7.1 Formatos

| Familia | Extensiones | Ruta de conversión | Vectorial | Imagen | Fase |
|---|---|---|---|---|---|
| PDF | pdf (incl. PDF/A, PDF/X, cifrados) | PyMuPDF directo; reparación MuPDF/qpdf | sí | slim | F1 |
| Raster común | png, jpg/jpeg, tif/tiff (multipágina, G4, LZW, 16 bits), bmp, gif (1er fotograma), webp | libvips | — | slim | F1 |
| Raster moderno | heic/heif, avif, jp2/j2k | pillow-heif / libvips / Pillow (OpenJPEG) | — | slim | F2 |
| SVG | svg, svgz | `rsvg-convert` → PDF; Inkscape como respaldo de alta fidelidad | sí | slim (rsvg) / full (Inkscape) | F2 |
| PostScript | ps, eps | Ghostscript `pdfwrite` (`-dSAFER`, `-dEPSCrop` para EPS) | sí | slim | F2 |
| Illustrator | ai | Si es compatible con PDF → ruta PDF; si es PostScript → Ghostscript | sí | slim | F2 |
| XPS | xps, oxps | PyMuPDF (MuPDF lo abre directamente) | sí | slim | F2 |
| Office / ODF | docx, doc, xlsx, xls, pptx, ppt, odt, ods, odp, odg, rtf, vsd, vsdx, pub | LibreOffice headless `--convert-to pdf` | sí | full | F2 |
| Metarchivos | emf, wmf | LibreOffice Draw (Inkscape como respaldo) | sí | full | F2 |
| CorelDRAW | cdr | LibreOffice (libcdr); fidelidad variable, se avisa | sí | full | F3 |
| DXF | dxf | ezdxf (add-on `drawing`, backend PyMuPDF): espacio papel (layouts) o modelo a escala 1:N | sí | slim | F3 |
| DWG | dwg | LibreDWG `dwg2dxf` → ruta DXF. Opcional: ODA File Converter aportado por el usuario (`A0TD_ODA_CONVERTER`, necesita `xvfb-run`) | sí | full | F3 |
| HPGL / plóter | plt, hpgl, hpg | Inkscape (importación HPGL) → PDF. Comprobar que sus extensiones Python funcionan en la imagen. | sí | full | F3 |
| DjVu | djvu, djv | `ddjvu -format=tiff` → ruta raster | — | full | F3 |

Para CAD (F3), la UI pide el layout (Modelo, Presentación1…). En espacio modelo pide unidades
(`$INSUNITS`) y escala 1:N, con política de color: color, negro (monocromo) o gris. Se recomienda
exportar a PDF desde el programa de CAD cuando sea posible.

### 7.2 Interfaz de conversores

```python
class Converter(Protocol):
    id: str                               # "pdf", "raster", "office", "svg", ...
    handles: frozenset[str]               # ids de formato detectado (Apéndice E)
    def available(self) -> bool: ...      # binarios presentes en esta imagen
    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument: ...
```

Hay un registro de conversores. `/api/v1/info` lista los disponibles. Cada conversor externo se ejecuta
como subproceso (lista de argumentos, sin shell), con timeout, límite de memoria, `HOME`/`TMPDIR` dentro
de `work/` y un perfil de LibreOffice propio por trabajo (`-env:UserInstallation=file://…`).

### 7.3 Documento canónico

- **PDF** (`canonical.pdf`) para todo lo vectorial o mixto: descifrado, reparado, anotaciones aplanadas,
  `/Rotate` incorporada al contenido (ninguna página rotada) y origen en la esquina de la CropBox. Las demás
  cajas se guardan en metadatos en coordenadas canónicas.
- **Raster** (`page-{n}.tif`) para imágenes: TIFF teselado con deflate (acceso aleatorio para recortes),
  sRGB 8 bits, gris 8 bits o 1 bit según el origen; alfa aplanado sobre blanco. Se guardan píxeles, PPP y
  `dpi_source` (`metadata`, `assumed` o `user`).
- Además, por página: `preview.webp` (lado mayor `A0TD_PREVIEW_MAX_PX`, por defecto 4096), `thumb.webp`
  (512 px) y `occupancy.png` (§9.6).

## 8. Interfaz de usuario

SPA servida por el propio contenedor. Escritorio primero; en tableta funciona entera; en móvil, el flujo
básico (subir → preset → descargar).

### 8.1 Pantallas y rutas

| Ruta | Pantalla | Contenido |
|---|---|---|
| `/` | Inicio | Zona de subida, formatos admitidos, presets rápidos, documentos recientes, botón "Probar con un documento de ejemplo" |
| `/documents/:id` | Editor | Vista previa con rejilla y panel de ajustes (§8.2) |
| `/jobs/:id` | Resultado | Progreso, miniaturas de páginas, descargas, Imprimir, hoja de prueba, guardar como preset |
| `/printers`, `/printers/:id/calibrate` | Impresoras | CRUD de perfiles y asistente de calibración (F2) |
| `/presets` | Presets | Integrados y de usuario, importar y exportar (F2) |
| `/history` | Historial | Documentos y trabajos (F2) |
| `/settings` | Ajustes | Idioma, unidades, valores por defecto, información del sistema |
| `/help` | Ayuda | Cómo imprimir al 100 % por visor, cómo montar, formatos, preguntas frecuentes, licencias de terceros |
| `/login` | Acceso | Solo si `A0TD_AUTH_MODE=password` |

### 8.2 Editor

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ a0-to-domestic   Inicio · Impresoras · Presets · Historial · Ayuda    ES ▾ ◐ │
├────────────────┬───────────────────────────────────────────┬─────────────────┤
│ DOCUMENTO      │                                           │ PAPEL           │
│ plano.pdf      │     ┌─────┬─────┬─────┐                   │ Impresora   ▾   │
│ A0 vertical    │     │ A1  │ A2  │ A3  │                   │ A4 ▾  ◉Auto     │
│ 841×1189 mm    │     ├─────┼─────┼─────┤  vista previa     │ ESCALA          │
│ Vectorial      │     │ B1  │ B2  │ B3  │  + rejilla SVG    │ ◉100% ○Una hoja │
│ Página 1/1 ▾   │     ├─────┼─────┼─────┤                   │ ○Rejilla ○…     │
│                │     │  …  │  …  │  …  │                   │ MOSAICO       ▸ │
│ ⚠ Avisos (1)   │     └─────┴─────┴─────┘                   │ MARCAS        ▸ │
│ Alternativas ▸ │  [−][+][Encajar] ☑Márgenes ☑Solapes ☑IDs  │ COLOR         ▸ │
│                │                                           │ SALIDA        ▸ │
├────────────────┴───────────────────────────────────────────┴─────────────────┤
│ 21 hojas A4 horizontal · 3×7 · 100 % · 841×1189 mm   [Hoja de prueba] [Generar]│
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Vista previa:** imagen de la página en el espacio de vista con una capa SVG cuyo `viewBox` está en mm.
  Muestra teselas, área útil, solapes, IDs, hojas vacías atenuadas y la región. Zoom y paneo con rueda y
  pellizco.
- **Interacciones:** clic en una tesela → incluir o excluir (F2). Arrastrar la rejilla → desplazamiento
  acotado (F2). Herramienta de región (F2). Doble clic en una tesela → vista previa exacta de esa hoja con
  sus marcas (render del servidor).
- **Recálculo en vivo:** cada cambio llama a `POST /api/v1/plan` con un debounce de 150 ms. La barra
  inferior se actualiza en < 300 ms.
- **Avisos:** lista con acción sugerida (p. ej. `LOW_DPI` → "Reducir escala" / "Ignorar").
- **Alternativas (F2):** tabla papel × orientación → hojas, y sugerencias de ahorro (§5.11).
- **Raster sin tamaño fiable:** diálogo "¿Qué tamaño representa esta imagen?" con A0 por defecto, otros
  papeles, ancho, alto o PPP.
- **Persistencia local:** últimos ajustes por documento e idioma en `localStorage`, envuelto en try/catch.

### 8.3 Calidad de la UI

- i18n con claves (`es` por defecto, `en` en F2), sin textos fijos en el código. Números con el formato de
  la configuración regional (`Intl.NumberFormat`).
- Unidades mm, cm o in en toda la UI; internamente siempre mm.
- Accesibilidad WCAG 2.1 AA: navegable por teclado, foco visible, etiquetas ARIA, contraste. La rejilla
  tiene una alternativa textual (lista de hojas).
- Tema claro y oscuro (sigue al sistema, con conmutador).
- Errores con el `code` del servidor traducido y una acción concreta.

## 9. Arquitectura

### 9.1 Stack

| Capa | Elección | Motivo |
|---|---|---|
| Backend | Python 3.13, FastAPI, Uvicorn, Pydantic v2, pydantic-settings | Mejor ecosistema PDF, imagen y CAD; Pydantic genera el contrato |
| PDF | PyMuPDF | Imposición vectorial (`show_pdf_page` con `clip`), render, análisis; abre PDF, XPS, SVG e imágenes |
| Raster | pyvips (libvips) | Imágenes enormes en streaming con poca RAM (un A0 a 600 PPP son 558 Mpx) |
| Otros | Pillow + pillow-heif, ezdxf (F3), numpy, Typer (CLI) | |
| Externos | LibreOffice, Inkscape, librsvg, Ghostscript, qpdf, LibreDWG, djvulibre | Conversión de formatos |
| Persistencia | SQLite (SQLAlchemy 2 + Alembic) + archivos en volumen | Sin servicios extra |
| Frontend | React + TypeScript + Vite, Mantine, TanStack Query, Zustand, react-i18next, react-zoom-pan-pinch | Productividad y componentes completos |
| Contrato | OpenAPI de FastAPI → tipos TS con `openapi-typescript` | Una sola fuente de verdad |
| Pruebas | pytest, Hypothesis, Vitest + Testing Library, Playwright | |
| Calidad | ruff, mypy, ESLint, Prettier | |

### 9.2 Componentes

```
                    ┌──────────────────────────── contenedor a0-to-domestic ────────────────────────────┐
 Navegador ───────► │ Uvicorn / FastAPI                                                                 │
 (SPA React)  ◄─SSE─│  ├─ sirve la SPA estática                                                         │
                    │  ├─ /api/v1  ── planificador (dominio puro, en proceso)                           │
                    │  ├─ gestor de trabajos ── 1 subproceso por trabajo ──► worker (python -m a0td…)   │
                    │  │     (semáforo A0TD_WORKERS, timeout, límite de RAM, cancelación)               │
                    │  └─ SQLite /data/app.db (solo escribe el proceso API)                             │
                    │                                                                                    │
                    │ worker: ingesta  = detectar → convertir → canónico → analizar → vista previa       │
                    │         render   = plan → imponer (PyMuPDF | pyvips) → marcas → montaje → empaquetar│
                    │         print    = IPP (F3)                                                        │
                    │ conversores: LibreOffice · Inkscape · rsvg · Ghostscript · LibreDWG · ezdxf · ddjvu │
                    │ volumen /data: documents/ · jobs/ · tmp/ · fonts/ · inbox/ · outbox/               │
                    └────────────────────────────────────────────────────────────────────────────────────┘
                                        │ (F3) IPP ──► impresora de red
```

### 9.3 Estructura del repositorio

```
a0-to-domestic/
├─ SPEC.md  README.md  .gitattributes  .editorconfig  .dockerignore
├─ compose.yml  compose.dev.yml
├─ .devcontainer/devcontainer.json
├─ docker/  Dockerfile  entrypoint.sh  libreoffice/registrymodifications.xcu
├─ backend/
│  ├─ pyproject.toml  uv.lock  alembic.ini
│  ├─ src/a0td/
│  │  ├─ config.py  logging.py  version.py
│  │  ├─ domain/     units.py papers.py settings.py planner.py naming.py geometry.py errors.py
│  │  ├─ ingest/     detect.py canonical.py analyze.py occupancy.py preview.py
│  │  │   └─ converters/  base.py registry.py pdf.py raster.py heif.py svg.py postscript.py xps.py
│  │  │                   office.py cad.py hpgl.py djvu.py
│  │  ├─ render/     engine.py vector.py raster.py marks.py assembly.py calibration.py color.py outputs.py
│  │  ├─ jobs/       manager.py worker.py limits.py events.py
│  │  ├─ storage/    db.py models.py files.py retention.py migrations/
│  │  ├─ printing/   ipp.py
│  │  ├─ automation/ hotfolder.py
│  │  ├─ api/        app.py deps.py errors.py security.py routers/{documents,plan,jobs,papers,printers,presets,system,auth}.py
│  │  └─ cli/        main.py
│  └─ tests/  unit/ property/ integration/ golden/ security/ conftest.py
├─ frontend/
│  ├─ package.json  vite.config.ts  tsconfig.json  index.html
│  └─ src/  main.tsx  app/  api/(schema.d.ts generado, client.ts)  components/  lib/(units.ts, format.ts)
│           i18n/(es.json, en.json)  features/{home,upload,editor,result,printers,presets,history,settings,help}/
├─ e2e/        playwright.config.ts  tests/
├─ tools/      make_fixtures.py
├─ fixtures/   (generados por tools/make_fixtures.py; versionar solo los pequeños)
└─ docs/       user-guide.md  development.md  adr/
```

`domain/` no importa nada de `ingest`, `render`, `api` ni `storage`, ni librerías de E/S. `render/` y
`ingest/` no conocen HTTP. `api/` solo orquesta.

### 9.4 Flujo de datos

```
subida (stream a disco + SHA-256) → documento "processing" → trabajo ingest
  → detect → converter → canónico → analyze (+ ocupación) → preview → documento "ready"
UI ⇄ POST /plan (puro, en proceso)  ·  POST /plan/sheet-preview (render de una hoja a baja resolución)
POST /jobs → trabajo render → plan por página → motor vector|raster → marcas → plano de montaje
  → PDF / ZIP en jobs/{id}/output → SSE "done" → descarga o impresión
```

### 9.5 Ejecución de trabajos

- Tipos: `ingest`, `render`, `print` (F3).
- Cada trabajo es un subproceso `python -m a0td.jobs.worker --job <id>` con `RLIMIT_AS` =
  `A0TD_JOB_MEMORY_MB`, timeout `A0TD_JOB_TIMEOUT_S`, `nice 10` y directorio temporal propio en
  `/data/tmp/<job>` que se borra al terminar.
- El worker escribe progreso por stdout en líneas JSON:
  `{"type":"progress","stage":"render","pct":42,"message_key":"render.sheet","params":{"k":9,"n":21}}` y al
  final `{"type":"result",…}` o `{"type":"error","code":…}`. El gestor actualiza la BD (único escritor de
  SQLite) y lo reenvía por SSE.
- Cancelar = SIGTERM, y SIGKILL a los 5 s, a todo el grupo de procesos (LibreOffice incluido).
- Al arrancar: los trabajos `running` pasan a `failed` (`INTERRUPTED`) y los `queued` se reencolan.
- La interfaz `JobQueue` es abstracta, por si algún día se quiere Redis/RQ para escalar.

### 9.6 Mapa de ocupación y hojas vacías

- Durante la ingesta se renderiza cada página a 2 px/mm (≈ 50.8 PPP) con antialiasing.
- Un píxel es contenido si su luminancia < `fondo − Δ`. El fondo es el percentil 99 de luminancia, porque
  los escaneos amarillean. Δ depende de la sensibilidad: strict 3, normal 12, tolerant 30.
- Se eliminan motas aisladas (apertura morfológica de 1 px) solo en orígenes raster.
- Se guarda como PNG de 1 bit. El planificador recibe un `OccupancyMap` con `any_in(rect_mm)` y
  `content_bbox()`. Una tesela es vacía si no hay contenido en su rectángulo de origen completo, solape
  incluido.

### 9.7 Motores de render

- **Vectorial:** `page.show_pdf_page(rect_destino, canonical, pno, clip=src_rect ∩ página)`. Hay que
  calcular `rect_destino` para la intersección, de modo que la escala sea exactamente `s`. PyMuPDF reutiliza
  el mismo XObject en todas las teselas.
- **Raster:** `pyvips` `extract_area` del rectángulo de la tesela, redondeado hacia fuera a píxeles. Se
  reescala solo si los PPP efectivos superan `max_dpi` y se inserta en el rectángulo exacto que corresponde
  a los píxeles redondeados (la geometría se mantiene exacta), recortado al área útil.
- **Rasterizar salida, B/N, invertir o blanquear:** la página se rasteriza una vez a `dpi` (PyMuPDF), se
  procesa con pyvips y continúa por el motor raster.
- **Escala de grises vectorial:** Ghostscript sobre una copia del canónico, cacheada por trabajo.
- Marcas con primitivas vectoriales de PyMuPDF (Helvetica base-14, sin incrustar fuentes).
- `ViewerPreferences` mediante `xref_set_key` en el catálogo.

## 10. API HTTP

Prefijo `/api/v1`, JSON y OpenAPI en `/api/docs`. Errores en formato `application/problem+json`
(RFC 9457): `{type, title, status, detail, code, params, errors?}`.

| Método | Ruta | Descripción | Fase |
|---|---|---|---|
| POST | `/documents` | multipart `file`, opcional `password` → 202 `Document` | F1 |
| GET | `/documents` | Recientes | F1 |
| GET | `/documents/{id}` | `Document` con estado `processing`, `password_required`, `ready` o `failed` | F1 |
| POST | `/documents/{id}/password` | `{password}` → 202 | F1 |
| POST | `/documents/{id}/pages/{n}/physical-size` | `{paper}` \| `{width_mm}` \| `{height_mm}` \| `{dpi}` → `Page` | F1 |
| DELETE | `/documents/{id}` | Borra el documento y sus trabajos | F1 |
| GET | `/documents/{id}/pages/{n}/preview` | `?max_px=` → image/webp | F1 |
| GET | `/documents/{id}/pages/{n}/thumb` | image/webp | F1 |
| GET | `/documents/{id}/events` | SSE de la ingesta | F1 |
| POST | `/plan` | `{settings, page_index, include_alternatives?}` → `Plan` | F1 |
| POST | `/plan/sheet-preview` | `{settings, page_index, sheet_id, max_px}` → image/png | F1 |
| POST | `/jobs` | `{settings, outputs?, test_sheet_only?}` → 202 `Job` | F1 |
| GET | `/jobs`, `/jobs/{id}` | Lista y detalle | F1 |
| GET | `/jobs/{id}/events` | SSE: `progress`, `done`, `error` | F1 |
| POST | `/jobs/{id}/cancel` | 202 | F1 |
| DELETE | `/jobs/{id}` | 204 | F1 |
| GET | `/jobs/{id}/outputs/{name}` | Descarga; `?inline=1` para abrir el PDF en el visor | F1 |
| GET | `/jobs/{id}/outputs/{name}/pages/{n}/thumb` | image/webp | F1 |
| POST | `/jobs/{id}/print` | `{printer_id, copies, sheets?}` → 202 | F3 |
| GET | `/papers` | Catálogo | F1 |
| GET/POST/PUT/DELETE | `/printers`, `/printers/{id}` | Perfiles (`GET` en F1 con los integrados) | F1/F2 |
| GET | `/printers/{id}/calibration.pdf` | `?paper=` | F2 |
| POST | `/printers/{id}/calibration` | `{paper, measured_x_mm, measured_y_mm, nominal_x_mm, nominal_y_mm, margins_mm}` | F2 |
| POST | `/printers/ipp-capabilities` | `{uri}` → papeles y márgenes | F3 |
| GET/POST/PUT/DELETE | `/presets`, `/presets/{id}` | (`GET` en F1 con los integrados) | F1/F2 |
| GET | `/info` | Versión, conversores, límites, modo de autenticación | F1 |
| POST | `/auth/login`, `/auth/logout` | Solo si `AUTH_MODE=password` | F2 |
| GET | `/healthz`, `/readyz`, `/metrics` | Fuera de `/api/v1`; `/metrics` en F3 | F1/F3 |

**Eventos SSE:**

```
event: progress   data: {"stage":"convert","pct":35,"message_key":"ingest.convert","params":{}}
event: done       data: {"status":"succeeded","document"|"job": {...}}
event: error      data: {"code":"CONVERSION_TIMEOUT","detail":"..."}
```

Como respaldo, la UI consulta `GET /jobs/{id}` cada segundo.

**Códigos de error:** `UNSUPPORTED_FORMAT`, `FORMAT_NOT_AVAILABLE_IN_IMAGE`, `FILE_TOO_LARGE`,
`TOO_MANY_PAGES`, `PIXEL_LIMIT_EXCEEDED`, `PAGE_TOO_LARGE`, `PASSWORD_REQUIRED`, `PASSWORD_INVALID`,
`PRINT_NOT_ALLOWED`, `CORRUPT_FILE`, `CONVERSION_FAILED`, `CONVERSION_TIMEOUT`, `INVALID_SETTINGS` (con
`errors[]` por campo), `OVERLAP_TOO_LARGE`, `CONTENT_AREA_TOO_SMALL`, `SCALE_OUT_OF_RANGE`,
`TOO_MANY_SHEETS`, `DOCUMENT_NOT_READY`, `NOT_FOUND`, `JOB_CANCELLED`, `INTERRUPTED`,
`PRINTER_UNREACHABLE`, `AUTH_REQUIRED`.

**Códigos de aviso** (`Plan.warnings`, `Page.warnings`): `LOW_DPI`, `VERY_LOW_DPI`,
`PHYSICAL_SIZE_ASSUMED`, `NONSTANDARD_SIZE`, `SMALL_TEXT`, `THIN_LINES`, `MANY_SHEETS` (> 50),
`FONTS_SUBSTITUTED`, `ANNOTATIONS_FLATTENED`, `LAYERS_PRESENT`, `DARK_BACKGROUND`, `PDF_REPAIRED`,
`LOW_FIDELITY_FORMAT`, `BOX_FALLBACK` (la caja pedida no existe; se usa `crop`).

## 11. Modelo de datos

### 11.1 Entidades (SQLite)

| Tabla | Campos clave |
|---|---|
| `documents` | `id` (uuid4), `name`, `format`, `mime`, `size_bytes`, `sha256`, `status`, `error_code`, `error_detail`, `pages_json`, `warnings_json`, `created_at`, `expires_at` |
| `jobs` | `id`, `document_id`, `kind` (ingest, render o print), `status` (queued, running, succeeded, failed o cancelled), `stage`, `progress`, `settings_json`, `plans_json`, `outputs_json`, `error_code`, `error_detail`, `created_at`, `started_at`, `finished_at`, `expires_at` |
| `printers` | `id`, `name`, `kind` (inkjet, laser u other), `builtin`, `papers_json` (papel → márgenes, sin bordes), `default_margins_json`, `color`, `scale_x`, `scale_y`, `ipp_uri`, `defaults_json`, timestamps |
| `presets` | `id`, `name`, `builtin`, `settings_json` (JobSettings parcial), timestamps |
| `app_settings` | `key`, `value_json` |

`Page` (dentro de `pages_json`): `index`, `width_mm`, `height_mm`, `boxes_mm` {crop, trim?, bleed?, media},
`kind`, `standard_match` {id, orientation, delta_mm} o null, `px` {w, h} (raster), `dpi`, `dpi_source`,
`layers[]` {xref, name, on}, `has_text`, `warnings[]`.

### 11.2 Archivos en `/data`

```
/data/app.db
/data/documents/{doc_id}/original.<ext> · canonical.pdf | page-{n}.tif · pages/{n}/{preview.webp,thumb.webp,occupancy.png} · meta.json
/data/jobs/{job_id}/output/*.pdf|*.zip · log.jsonl
/data/tmp/{job_id}/        (temporal; se borra)
/data/fonts/               (opcional, fuentes de usuario)
/data/inbox/ /data/outbox/ (F3, carpeta vigilada)
```

Nunca se usa el nombre de archivo del cliente en rutas; solo se muestra y se usa en `Content-Disposition`
(RFC 5987).

### 11.3 JobSettings y Plan

`JobSettings` (Apéndice B) es un modelo Pydantic versionado (`schema_version`). Lo comparten la API, la
CLI y los presets (un preset es un JobSettings parcial que se fusiona sobre los valores por defecto).

`Plan` (por página):

```
page_index, view_size_mm[w,h], source_rect_mm[x0,y0,x1,y1], orientation, paper{id,width_mm,height_mm},
margins_mm{top,right,bottom,left} (efectivos), content_mm[w,h], overlap_mm, step_mm[w,h], scale,
scale_label ("100 %", "1:100"), grid{cols,rows}, coverage_mm[w,h], origin_mm[x,y],
offset_range_mm{x[min,max],y[min,max]}, assembled_size_mm[w,h], effective_dpi|null,
sheets[{id,row,col,print_index|null,src_rect_mm,blank,selected,neighbors{top,right,bottom,left},
        cut_edges[],receive_edges[]}],
counts{total,printed,blank,deselected}, alternatives[]?, savings[]?, warnings[{code,severity,params}]
```

## 12. Docker y entornos

### 12.1 Imagen (multi-stage, `docker/Dockerfile`)

| Etapa / target | Contenido |
|---|---|
| `frontend-build` | `node:24-slim`: `npm ci && npm run build` → `dist/` |
| `libredwg-build` | Compila LibreDWG (`dwg2dxf`) desde el código fuente |
| `base` | `python:3.13-slim-trixie` (fijar digest), `tini`, usuario `app` (UID 10001), entorno virtual con las dependencias del backend (uv), libvips, fuentes básicas (DejaVu, Liberation, Carlito, Caladea, Noto core) |
| `slim` | `base` + Ghostscript + qpdf + librsvg (`rsvg-convert`) + código + `dist/`. Formatos: PDF, raster, HEIC/AVIF, SVG, PS/EPS/AI, XPS, DXF |
| `full` (por defecto) | `slim` + LibreOffice (writer, calc, impress, draw; `--no-install-recommends`, sin Java) + Inkscape + djvulibre + LibreDWG |
| `dev` | Dependencias de `full` + herramientas de desarrollo (uv, ruff, mypy, pytest, Node 24). El código se monta, no se copia |

- Nombres de paquetes Debian orientativos: hay que comprobarlos en trixie (renombrados `t64`).
- `ENTRYPOINT ["tini","--","/app/entrypoint.sh"]`: aplica las migraciones y arranca Uvicorn. Si el primer
  argumento es `a0td`, ejecuta la CLI.
- `HEALTHCHECK` a `/healthz` con Python (sin curl). `EXPOSE 8080`, `VOLUME /data`.
- `HOME=/tmp/home`, `XDG_CACHE_HOME=/tmp/cache` y `TMPDIR=/data/tmp`, para que funcione con el sistema de
  archivos raíz en solo lectura.
- LibreOffice con macros desactivadas (`MacroSecurityLevel=3`, `DisableMacrosExecution=true`) mediante
  `registrymodifications.xcu`.
- `ARG A0TD_VERSION` → `/api/v1/info`.
- Objetivos de tamaño: `slim` ≤ 600 MB, `full` ≤ 2 GB.
- Multi-arquitectura `linux/amd64` y `linux/arm64` con buildx (F3). ODA solo existe para amd64.

### 12.2 `compose.yml` (uso)

```yaml
services:
  app:
    build: { context: ., dockerfile: docker/Dockerfile, target: full }
    image: a0-to-domestic:latest
    ports: ["127.0.0.1:8080:8080"]      # solo local; para LAN, quitar 127.0.0.1 y activar A0TD_AUTH_MODE
    volumes: ["a0data:/data"]
    environment:
      A0TD_WORKERS: "2"
      A0TD_RETENTION_HOURS: "72"
    read_only: true
    tmpfs: ["/tmp:size=512m"]
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    mem_limit: 4g
    cpus: 4
    restart: unless-stopped
volumes:
  a0data:
```

### 12.3 Desarrollo (`compose.dev.yml` y Dev Container)

- Servicio `backend` (target `dev`): `uvicorn --reload` en el puerto 8000, con `./backend` montado.
- Servicio `frontend` (`node:24-slim`): Vite en el puerto 5173 con proxy `/api` → backend.
  `node_modules` va en un volumen con nombre (rendimiento en Windows).
- Perfil `e2e`: imagen oficial de Playwright contra `full`.
- `.devcontainer/devcontainer.json` reutiliza `compose.dev.yml` (VS Code).
- Comandos canónicos (todo dentro de contenedores):

```
docker compose -f compose.dev.yml up                                   # entorno de desarrollo
docker compose -f compose.dev.yml run --rm backend pytest              # tests backend
docker compose -f compose.dev.yml run --rm backend ruff check . && … mypy src
docker compose -f compose.dev.yml run --rm frontend npm test           # tests frontend
docker compose -f compose.dev.yml --profile e2e run --rm e2e           # E2E
docker build -f docker/Dockerfile --target full -t a0-to-domestic:dev .
```

- **Windows:** los bind mounts desde NTFS son lentos. Se acepta en este proyecto; para más velocidad,
  clonar dentro de WSL2. `.gitattributes` fuerza LF (`* text=auto eol=lf`), imprescindible para
  `entrypoint.sh`.

### 12.4 Variables de entorno

| Variable | Defecto | Descripción |
|---|---|---|
| `A0TD_PORT` | 8080 | Puerto HTTP |
| `A0TD_DATA_DIR` | /data | Raíz de datos |
| `A0TD_TMP_DIR` | /data/tmp | Temporales de trabajos |
| `A0TD_MAX_UPLOAD_MB` | 1024 | Tamaño máximo de subida |
| `A0TD_MAX_PAGES` | 100 | Páginas máximas por documento |
| `A0TD_MAX_MEGAPIXELS` | 1000 | Límite por imagen (anti-bomba) |
| `A0TD_MAX_PAGE_MM` | 10000 | Lado máximo de página |
| `A0TD_MAX_SHEETS` | 500 | Hojas máximas por trabajo |
| `A0TD_WORKERS` | 2 | Trabajos concurrentes |
| `A0TD_JOB_TIMEOUT_S` | 600 | Timeout por trabajo |
| `A0TD_JOB_MEMORY_MB` | 3072 | Límite de RAM por trabajo |
| `A0TD_RETENTION_HOURS` | 72 | Caducidad de documentos y trabajos |
| `A0TD_PREVIEW_MAX_PX` | 4096 | Lado mayor de la vista previa |
| `A0TD_AUTH_MODE` | none | `none`, `password` o `token` |
| `A0TD_PASSWORD_HASH` / `A0TD_TOKEN` | — | Hash Argon2 de la contraseña / token |
| `A0TD_DEFAULT_LOCALE` | es | Idioma por defecto |
| `A0TD_ENABLE_IPP` | false | Impresión directa (F3) |
| `A0TD_ODA_CONVERTER` | — | Ruta al ODA File Converter montado (F3) |
| `A0TD_HOTFOLDER` | false | Carpeta vigilada (F3) |
| `A0TD_LOG_LEVEL` / `A0TD_LOG_FORMAT` | info / json | Registro |
| `A0TD_STATIC_DIR` | /app/static | Carpeta de la SPA compilada (vacía = no servir SPA) |
| `A0TD_SECRET_KEY` | — | Clave HMAC de sesión y CSRF (≥ 32 bytes). Si falta, se genera y persiste en `/data` |
| `A0TD_TRUSTED_PROXIES` | — | IPs de proxies de confianza para `X-Forwarded-*`. Vacío = no se confía en ninguno |
| `A0TD_IPP_ALLOW_HOSTS` | — | Hosts o IPs exentos de la política SSRF de IPP (por defecto se rechazan loopback, link-local y metadatos) |
| `A0TD_IPP_INSECURE_TLS` | false | Desactiva la verificación TLS en `ipps`/`https` (impresoras con certificado autofirmado) |
| `A0TD_HOTFOLDER_INTERVAL_S` | 5 | Intervalo de sondeo de la carpeta vigilada |
| `A0TD_METRICS` | false | Expone `/metrics`; con autenticación activa exige sesión o token |
| `A0TD_CAD_MAX_ENTITIES` | 2000000 | Máximo de entidades en DXF/DWG |

## 13. Requisitos no funcionales

Hardware de referencia: 4 vCPU, 8 GB de RAM, SSD.

| Área | Requisito |
|---|---|
| Rendimiento | `POST /plan` p95 < 50 ms. Vista previa tras un cambio < 300 ms. Ingesta de un PDF vectorial A0 ≤ 20 MB < 5 s hasta la vista previa. Raster A0 a 300 PPP (≈ 140 Mpx) < 20 s con pico de RAM < 1.5 GB. Render A0 vectorial → 21 A4 + montaje < 10 s. Raster → 21 A4 < 60 s. PPTX A0 < 60 s. Arranque hasta `/readyz` < 10 s. |
| Precisión | Error en coordenadas PDF ≤ 0.05 mm. Prueba de reensamblado ≤ 1 px a 150 PPP fuera de las costuras. |
| Tamaño de salida | PDF vectorial ≤ 1.2 × el origen + 200 KB. |
| Fiabilidad | Trabajos aislados en subprocesos: un fallo no tumba la API. Reinicio seguro (§9.5). Migraciones automáticas e idempotentes. |
| Privacidad | Sin telemetría ni llamadas externas. Los registros no contienen el contenido de los documentos. |
| Observabilidad | Logs JSON con `request_id`, `document_id`, `job_id`, `stage` y `duration_ms`. `/api/v1/info`. `/metrics` (F3). |
| Compatibilidad | Últimas 2 versiones de Chrome, Edge, Firefox y Safari. Docker Desktop en Windows y macOS, Docker Engine en Linux. |
| Mantenibilidad | Cobertura: `domain/` ≥ 95 %, total ≥ 80 %. mypy estricto en `domain/` y `render/`. Nuevos formatos sin tocar el núcleo (registro de conversores). |
| Usabilidad | WCAG 2.1 AA. Español e inglés. Toda acción larga muestra progreso y se puede cancelar. |

## 14. Seguridad

Todos los archivos subidos se tratan como **no confiables**.

- **Contenedor:** usuario no root, sistema de archivos raíz en solo lectura, `no-new-privileges`,
  `cap_drop: ALL`, puerto ligado a `127.0.0.1` por defecto, límites de CPU y RAM.
- **Subidas:** streaming a disco cortando al superar el límite, detección por firma, IDs aleatorios y
  nombres saneados.
- **Anti-bombas:** límites de megapíxeles (`pyvips` y `Image.MAX_IMAGE_PIXELS` de Pillow), páginas, lado de
  página, hojas y tiempo.
- **Conversores:** subprocesos sin shell con timeout, `RLIMIT_AS` y directorio de trabajo propio.
  Ghostscript con `-dSAFER`. LibreOffice sin macros. SVG sin recursos externos (rsvg por defecto; con
  Inkscape se rechazan `href` remotos). Analizadores XML propios con `defusedxml`.
- **PDF de salida:** sin JavaScript, acciones, adjuntos del origen ni anotaciones (solo se copia el
  contenido de las páginas y el `a0td-job.json` propio).
- **Web:** CSP estricta (`default-src 'self'`), `X-Content-Type-Options: nosniff`,
  `frame-ancestors 'none'`, `Referrer-Policy: no-referrer`. Sin CORS (mismo origen). Con
  `AUTH_MODE=password`: cookie `HttpOnly; SameSite=Strict`, token CSRF y límite de intentos de login.
- **Importación por URL:** no se implementa, para evitar SSRF.
- **Red en conversiones:** LibreOffice no bloquea por sí mismo los recursos remotos enlazados en OOXML. En F3
  los conversores se ejecutarán sin salida de red: espacio de nombres de red propio, o servicio conversor en
  una red `internal: true`. Hasta entonces, se recomienda desplegar el contenedor sin acceso a Internet.
- **Cadena de suministro:** dependencias fijadas (`uv.lock`, `package-lock.json`), imagen base fijada por
  digest. En CI: `pip-audit`, `npm audit`, Trivy sobre la imagen y SBOM con syft.
- **Permisos PDF:** se respetan; no se elude ninguna restricción.

## 15. Estrategia de pruebas

### 15.1 Fixtures

`tools/make_fixtures.py` genera documentos sintéticos de forma determinista:

- A0 vectorial con rejilla de 10 mm, cuadros de 100 mm y coordenadas impresas (cada hoja muestra qué
  coordenadas contiene, lo que permite verificarla a ojo).
- A0 con contenido solo en la mitad superior; A1 horizontal; página con `/Rotate 90`; CropBox ≠ MediaBox
  con TrimBox; PDF cifrado; PDF con anotaciones; PDF con capas OCG (tallas); multipágina con tamaños mixtos;
  PDF corrupto.
- Raster: TIFF G4 de 1 bit a 200 PPP, JPEG sin PPP (4000 × 5657), PNG con transparencia, JPEG CMYK, TIFF
  multipágina.
- F2 y F3: PPTX A0 (python-pptx), DOCX, SVG con y sin unidades físicas, EPS (Ghostscript), DXF (ezdxf),
  HEIC si es posible, y muestras maliciosas (§15.6).

### 15.2 Unitarias y de propiedades (dominio)

Hypothesis sobre orígenes, papeles, márgenes, solapes y escalas aleatorios. Invariantes:

1. La unión de las teselas cubre `R` por completo.
2. Teselas adyacentes se solapan exactamente `o` mm de papel.
3. Ninguna tesela excede el área útil.
4. Minimalidad: con `n_c − 1` columnas (o `n_r − 1` filas) no se cubre.
5. La orientación automática nunca da más hojas que la otra.
6. Centrado simétrico: holgura izquierda = derecha ± 1e-9.
7. Casos frontera `T = n·d + o` ⇒ exactamente `n`.
8. `fit_grid(c, r)` produce una rejilla de exactamente `c × r` o menos.
9. Los vectores del Apéndice C se cumplen exactamente.

### 15.3 Golden

Cada fixture se procesa con varios ajustes y el PDF resultante se renderiza a 72 PPP para compararlo con
PNG de referencia (diferencia de píxeles con tolerancia). Además se comprueban tamaños de página exactos
(± 0.01 pt), número de páginas y metadatos. Las referencias se regeneran con un flag explícito
(`--update-golden`).

### 15.4 Reensamblado (extremo a extremo de la geometría)

Se renderiza el área útil de cada hoja a 150 PPP y se compone en un lienzo según el plan, descontando el
solape. El resultado se compara con el render directo del origen a escala `s`: debe coincidir con
≤ 1 px de diferencia fuera de una banda de 2 px en las costuras. Se hace para el motor vectorial y para el
raster.

### 15.5 Integración, API, frontend y E2E

- Un test por conversor con su fixture: nº de páginas, tamaño ± tolerancia y que no salga en blanco.
  Los conversores no disponibles en la imagen se saltan con `skip`.
- API con el `TestClient` de httpx: flujo subir → ready → plan → job → descarga; errores y códigos;
  validación contra el esquema OpenAPI.
- Frontend con Vitest: unidades, formato, store del editor y componentes clave.
- E2E con Playwright contra la imagen `full`: subir un A0 → preset A4 → generar → descargar y comprobar 22
  páginas; flujo de contraseña; flujo de raster sin PPP.

### 15.6 Seguridad

Bomba de píxeles (PNG de 100k × 100k), PDF con 10 000 páginas, SVG con XXE y `href` remoto, PostScript
que intenta leer `/etc/passwd`, DOCX con macro, PDF con JavaScript y archivo con extensión engañosa. Se
espera rechazo o neutralización, sin cuelgues y sin escrituras fuera del directorio del trabajo.

### 15.7 Contenedor

- `slim`, `full` y `dev` construyen.
- `docker run --rm img a0td formats` lista los conversores esperados por target.
- El healthcheck pasa, el proceso corre como UID 10001 y funciona con `read_only: true`.

### 15.8 Protocolo manual de impresión (antes de cada versión)

Con una impresora de inyección y otra láser: imprimir la página de calibración y montar A0 → A4
(21 hojas). Medir 3 distancias conocidas (≥ 500 mm) en el montaje: error ≤ 0.5 % tras calibrar.

### 15.9 CI (GitHub Actions)

Lint y tipos → tests del backend en el contenedor `dev` → tests del frontend → build de `slim` y `full` →
E2E → Trivy y SBOM. Benchmarks de §13 cada noche con umbrales.

## 16. Plan de implementación

### 16.1 Fases

- **F0 Fundaciones:** repositorio, Docker, dominio y planificador probados, esqueleto de la UI.
- **F1 MVP:** PDF y raster, mosaico completo con marcas y plano de montaje, UI de punta a punta, CLI, E2E.
  *Salida:* CA-1 a CA-7 en verde y prueba física A0 → A4 correcta.
- **F2:** resto de formatos de F2, perfiles y calibración, opciones avanzadas de mosaico, color, salidas
  extra, presets, historial, inglés y autenticación.
- **F3:** CAD, HPGL y DjVu; capas y legibilidad; IPP; carpeta vigilada; banner; métricas; multi-arquitectura.

### 16.2 Paquetes de trabajo

| WP | Contenido | Depende de | Hecho cuando |
|---|---|---|---|
| WP-01 | Esqueleto: `.gitattributes`, `.editorconfig`, `backend/pyproject.toml`, `config.py`, logging, app FastAPI con `/healthz`, `/readyz` y `/api/v1/info`, `domain/` units, papers (Apéndice A), settings (Apéndice B, completo), naming y errors | — | `pytest` y `ruff` en verde en el contenedor `dev` |
| WP-02 | Planificador completo (§5, §5.11, §11.3) con tests de propiedades y Apéndice C | WP-01 | Invariantes de §15.2 en verde; cobertura de `domain/` ≥ 95 % |
| WP-03 | Docker: Dockerfile (todos los targets), `entrypoint.sh`, `compose.yml`, `compose.dev.yml`, devcontainer, `.dockerignore`, configuración de LibreOffice | WP-01 | Los tres targets construyen; smoke test de §15.7 |
| WP-04 | Esqueleto del frontend: Vite, React, TS, Mantine, router, Query, i18n `es`, tema, layout y rutas vacías, cliente API con tipos generados | — | `npm run build` y `npm test` en verde |
| WP-05 | `tools/make_fixtures.py` (fixtures de F1) | WP-01 | Genera todos los fixtures de F1 de forma determinista |
| WP-06 | Ingesta F1: detect, conversores pdf y raster, canónico, analyze, ocupación, preview, contraseña, reparación y aplanado | WP-01, WP-05 | Tests de integración por fixture en verde |
| WP-07 | Render F1: motores vectorial y raster, marcas, plano de montaje, cuadro de prueba, ViewerPreferences, metadatos, nombres y hoja de prueba | WP-02, WP-06 | Golden y reensamblado en verde; CA-1 y CA-2 |
| WP-08 | Trabajos, almacenamiento y API F1: modelos, migraciones, gestor de subprocesos, SSE, endpoints F1, retención, errores, seguridad web | WP-06, WP-07 | Tests de API en verde; CA-4 a CA-6 |
| WP-09 | CLI F1 | WP-07 | Tests de CLI en verde dentro de la imagen |
| WP-10 | Frontend F1: inicio y subida, editor (vista previa y rejilla SVG, ajustes F1, avisos, barra de resumen, vista de hoja), resultado (progreso, miniaturas, descargas, imprimir, instrucciones), presets integrados, ayuda | WP-04, WP-08 | Tests de componentes en verde; flujo manual completo |
| WP-11 | E2E y tests de contenedor F1 | WP-03, WP-10 | §15.5 E2E y §15.7 en verde |
| WP-12 | Conversores F2 (office, svg, postscript, xps, heif/avif/jp2, emf/wmf) y fuentes | WP-06 | Tests por formato en verde en `full`; `skip` correcto en `slim` |
| WP-13 | Perfiles de impresora y calibración (backend y UI) | WP-08, WP-10 | CAL-01 a 03; PAP-04 y 05 |
| WP-14 | Mosaico F2 en la UI: selección de hojas, región, arrastre de rejilla, modos `final_size` y `drawing_scale`, alternativas y ahorro, recorte automático | WP-10 | MOS-03/05/07, ESC-02/04/05 |
| WP-15 | Color y salidas F2: grises, B/N, invertir, blanquear, rasterizar, ZIP, copia reducida | WP-07 | COL-02 a 05, SAL-04 a 06 |
| WP-16 | Presets de usuario, historial, ajustes, inglés y autenticación opcional | WP-08, WP-10 | PRE-02/03/05, ADM-03 |
| WP-17 | CAD y otros (DXF, DWG, HPGL, DjVu, CDR) | WP-12 | Tests por formato en verde en `full` |
| WP-18 | Análisis avanzado: capas OCG, legibilidad, escala detectada, grosor mínimo, tinta, capa de marcas | WP-07, WP-10 | ANA-07 a 09, COL-06/07, MAR-05 |
| WP-19 | IPP, carpeta vigilada, banner, `/metrics`, multi-arquitectura, `landscape_as_rotated_portrait` | WP-08 | IMP-03/04, CLI-02, PAP-06/07, ADM-04 |
| WP-20 | Documentación: README, guía de usuario (es), guía de desarrollo, ADRs y licencias de terceros | todos | Revisada |

### 16.3 Convenciones para quien implemente

- Código, identificadores y comentarios en inglés; textos de UI por i18n; documentación de usuario en español.
- Python: ruff (lint y formato, línea 100), tipado completo, mypy estricto en `domain/` y `render/`,
  Pydantic v2. Sin estado global salvo la configuración.
- TypeScript estricto, ESLint y Prettier, componentes funcionales, carpetas por funcionalidad, sin textos
  de UI fijos.
- Los tests no usan red. Todo se ejecuta dentro de los contenedores (§12.3).
- No modificar `.claude/` ni `.recall/`.
- Cualquier desviación de esta spec se documenta en `docs/adr/` y se refleja aquí.
- *Definition of done* de cada WP: lint, tipos y tests en verde dentro del contenedor; build de Docker si se
  tocan dependencias; documentación actualizada si cambia el comportamiento.

## 17. Riesgos

| Riesgo | Impacto | Mitigación |
|---|---|---|
| El usuario imprime con "Ajustar a página" (escala ~94–97 %) | Montaje erróneo | `PrintScaling /None`, instrucciones visibles, cuadro de prueba, hoja de prueba previa, validación en la calibración, IPP con `print-scaling=none` |
| Márgenes reales mayores que los supuestos | Franjas sin imprimir | 5 mm por defecto, calibración de márgenes, márgenes seguros en horizontal |
| Fidelidad de DWG y CDR | Planos incorrectos | Fase 3, avisos `LOW_FIDELITY_FORMAT`, ODA opcional, recomendar exportar a PDF |
| Fidelidad de Office (fuentes) | Texto desplazado | Fuentes métricamente compatibles (Carlito, Caladea, Liberation), `/data/fonts`, aviso `FONTS_SUBSTITUTED` |
| Memoria con raster enorme | OOM | libvips en streaming, límites, `RLIMIT_AS` y `mem_limit` |
| PDFs vectoriales muy complejos atascan la impresora | Impresión lenta o fallida | Opción de rasterizar la salida |
| Licencias AGPL (PyMuPDF, Ghostscript) y GPL (LibreDWG, Inkscape) | Obligaciones al distribuir | Decisión D1; página de licencias de terceros |
| Tamaño de la imagen `full` | Descarga lenta | Targets `slim` y `full`, `--no-install-recommends` |
| Docker Desktop en Windows: sin mDNS ni inotify en bind mounts | IPP y carpeta vigilada | URI IPP manual; sondeo en la carpeta vigilada |

## 18. Decisiones (adoptadas por defecto)

| # | Decisión | Adoptado | Alternativa |
|---|---|---|---|
| D1 | Licencia | AGPL-3.0, compatible con PyMuPDF y Ghostscript | Privado; o pypdfium2 + pikepdf para una licencia permisiva |
| D2 | Lenguaje del backend | Python | Java (PDFBox) o Node (pdf-lib + sharp): ecosistema más pobre para render y conversión |
| D3 | Despliegue | Una imagen con targets `slim` y `full` | Conversores en un servicio aparte con red interna, solo si se expone a Internet |
| D4 | Usuarios | Un usuario con contraseña opcional | Multiusuario (fuera de alcance) |
| D5 | DWG | F3 con LibreDWG; ODA aportado por el usuario | — |
| D6 | Impresión | Visor PDF (F1); IPP en F3 | — |
| D7 | Kit de UI | Mantine | shadcn/ui + Tailwind |

---

## Apéndice A — Catálogo de papeles

**Salida** (id → mm, vertical):

| id | Nombre | mm | in | Uso |
|---|---|---|---|---|
| `A3` | A3 | 297 × 420 | — | ISO |
| `A3PLUS` | A3+ / Super B | 329 × 483 | 13 × 19 (aprox.) | Fotográficas grandes |
| `A4` | A4 | 210 × 297 | — | ISO |
| `A5` | A5 | 148 × 210 | — | ISO |
| `A6` | A6 | 105 × 148 | — | ISO |
| `B4` | B4 (ISO) | 250 × 353 | — | ISO |
| `B5` | B5 (ISO) | 176 × 250 | — | ISO |
| `JIS_B4` | B4 (JIS) | 257 × 364 | — | Japón |
| `JIS_B5` | B5 (JIS) | 182 × 257 | — | Japón |
| `LETTER` | Carta / Letter | 215.9 × 279.4 | 8.5 × 11 | Norteamérica, Latinoamérica |
| `LEGAL` | Legal / Oficio US | 215.9 × 355.6 | 8.5 × 14 | Norteamérica, Latinoamérica |
| `OFICIO_MX` | Oficio (México) | 216 × 340 | 8.5 × 13.4 | México |
| `FOLIO` | Folio / Oficio 8.5×13 | 215.9 × 330.2 | 8.5 × 13 | Latinoamérica, Filipinas |
| `TABLOID` | Tabloide / Ledger | 279.4 × 431.8 | 11 × 17 | Norteamérica |
| `EXECUTIVE` | Ejecutivo | 184.15 × 266.7 | 7.25 × 10.5 | Norteamérica |
| `STATEMENT` | Media carta / Statement | 139.7 × 215.9 | 5.5 × 8.5 | Norteamérica |
| `PHOTO_10X15` | Foto 10×15 (4×6 in) | 101.6 × 152.4 | 4 × 6 | Foto |
| `PHOTO_13X18` | Foto 13×18 (5×7 in) | 127 × 177.8 | 5 × 7 | Foto |
| `PHOTO_20X25` | Foto 20×25 (8×10 in) | 203.2 × 254 | 8 × 10 | Foto |
| `CUSTOM` | Personalizado | 50–500 × 50–1500 | — | — |

Cada papel guarda su nombre PWG 5101.1 para IPP (p. ej. `iso_a4_210x297mm`, `na_letter_8.5x11in`); se
verificará al implementar IPP (F3).

**Tamaños de origen reconocidos** (para ANA-01): 4A0 1682 × 2378, 2A0 1189 × 1682, A0 841 × 1189,
A1 594 × 841, A2 420 × 594, A3, A4; B0 1000 × 1414, B1 707 × 1000, B2 500 × 707; ARCH E 914.4 × 1219.2,
ARCH E1 762 × 1066.8, ARCH D 609.6 × 914.4, ARCH C 457.2 × 609.6; ANSI E 863.6 × 1117.6,
ANSI D 558.8 × 863.6, ANSI C 431.8 × 558.8.

## Apéndice B — JobSettings

```json
{
  "schema_version": 1,
  "document_id": "6f1c2e0a-0000-4000-8000-000000000000",
  "source": {
    "pages": "all",
    "box": "crop",
    "rotate": 0,
    "mirror": false,
    "region_mm": null,
    "auto_trim": false,
    "hidden_layers": [],
    "visible_layers": []
  },
  "target": {
    "printer_id": "builtin-generic",
    "paper": "A4",
    "custom_paper_mm": null,
    "orientation": "auto",
    "margins_mm": null,
    "info_band": { "position": "none", "height_mm": 0 },
    "landscape_as_rotated_portrait": false
  },
  "scale": { "mode": "percent", "percent": 100 },
  "tiling": {
    "join": "overlap",
    "overlap_mm": 10,
    "align": { "x": "center", "y": "center" },
    "offset_mm": { "x": 0, "y": 0 },
    "order": "rows",
    "naming": "letter_number",
    "skip_blank": true,
    "blank_sensitivity": "normal",
    "only_sheets": null
  },
  "marks": {
    "cut_lines": true,
    "outer_cut_lines": false,
    "alignment_marks": true,
    "join_diamonds": true,
    "labels": true,
    "neighbor_hints": true,
    "ruler": true,
    "glue_shading": false,
    "test_square": "assembly_page",
    "assembly_page": "first",
    "color": "#808080",
    "line_width_pt": 0.5,
    "marks_layer": false
  },
  "color": {
    "mode": "color",
    "threshold": 160,
    "invert": false,
    "whiten_background": false,
    "min_line_width_mm": null
  },
  "output": {
    "formats": ["pdf"],
    "rasterize": false,
    "dpi": 300,
    "max_dpi": 600,
    "jpeg_quality": 90,
    "filename_template": "{name}_{paper}-{orientation}_{cols}x{rows}_{scale}pct",
    "include_reduced_copy": false
  }
}
```

**Enumeraciones y reglas:**

| Campo | Valores |
|---|---|
| `source.pages` | `"all"` o rangos `"1,3-5"` (base 1) |
| `source.box` | `crop`, `trim`, `bleed` o `media` (si no existe la caja pedida → `crop` y aviso) |
| `source.rotate` | `0`, `90`, `180` o `270` |
| `source.region_mm` | `null` o `{x, y, width, height}` en espacio de vista |
| `source.hidden_layers` / `source.visible_layers` | Capas OCG que se apagan o encienden de forma explícita. Las capas apagadas en el origen siguen apagadas salvo que figuren en `visible_layers` |
| `target.paper` | id del Apéndice A; `CUSTOM` exige `custom_paper_mm: {width, height}` |
| `target.orientation` | `auto`, `portrait` o `landscape` |
| `target.margins_mm` | `null` (los del perfil para ese papel) o `{top, right, bottom, left}` |
| `target.info_band.position` | `none`, `top` o `bottom` |
| `scale` | `{mode:"percent",percent}`, `{mode:"fit_sheet"}`, `{mode:"fit_grid",cols,rows}`, `{mode:"fit_count",max_sheets}`, `{mode:"final_size",width_mm \| height_mm}`, `{mode:"drawing_scale",from_denominator,to_denominator}` |
| `tiling.join` | `overlap` (`overlap_mm` 3–50) o `butt` (fuerza `overlap_mm = 0`) |
| `tiling.align.x/y` | `start`, `center` o `end` |
| `tiling.order` | `rows` o `columns` |
| `tiling.naming` | `letter_number`, `number_number` o `sequential` |
| `tiling.blank_sensitivity` | `strict`, `normal` o `tolerant` |
| `tiling.only_sheets` | `null` o lista de IDs (`["A1","B3"]`) |
| `marks.test_square` | `none`, `assembly_page` o `first_sheet` |
| `marks.assembly_page` | `none`, `first` o `last` |
| `color.mode` | `color`, `grayscale` o `bw` (`bw` usa `threshold`) |
| `output.formats` | lista de `pdf`, `png_zip`, `jpeg_zip` o `tiff_zip` |
| `output.dpi` | 150, 200, 300 o 600 (rasterizado, B/N e imágenes) |

**Presets integrados** (fusionados sobre los valores por defecto):

| Preset | Cambios |
|---|---|
| Tamaño real en A4 / Carta / A3 | `paper` = `A4` / `LETTER` / `A3` |
| Reducido a una A4 / A3 | `scale.mode=fit_sheet`; `assembly_page=none`; sin marcas |
| Póster 2×2 A3 | `paper=A3`, `scale={fit_grid,2,2}` |
| Patrón de costura 1:1 (A4, a tope) | `join=butt`, `join_diamonds=true`, `test_square=first_sheet`, `skip_blank=true` |

## Apéndice C — Vectores de prueba del planificador

Origen A0 vertical 841 × 1189 mm, márgenes 5 mm por lado, sin banda de información y, salvo que se
indique otra cosa, solape 10 mm y escala 100 %.

**C.1 Mosaico al 100 %**

| Papel | Orientación | Área útil | Paso | Rejilla (col × fil) | Hojas |
|---|---|---|---|---|---|
| A4 | vertical | 200 × 287 | 190 × 277 | 5 × 5 | 25 |
| A4 | horizontal | 287 × 200 | 277 × 190 | 3 × 7 | **21** ← auto |
| A3 | vertical | 287 × 410 | 277 × 400 | 3 × 3 | **9** ← auto |
| A3 | horizontal | 410 × 287 | 400 × 277 | 3 × 5 | 15 |
| Carta | vertical | 205.9 × 269.4 | 195.9 × 259.4 | 5 × 5 | **25** ← auto |
| Carta | horizontal | 269.4 × 205.9 | 259.4 × 195.9 | 4 × 7 | 28 |
| Legal | vertical | 205.9 × 345.6 | 195.9 × 335.6 | 5 × 4 | **20** ← auto |
| Legal | horizontal | 345.6 × 205.9 | 335.6 × 195.9 | 3 × 7 | 21 |
| Tabloide | vertical | 269.4 × 421.8 | 259.4 × 411.8 | 4 × 3 | **12** ← auto |
| Tabloide | horizontal | 421.8 × 269.4 | 411.8 × 259.4 | 3 × 5 | 15 |

- **Casos frontera:** A4 horizontal y A3 vertical dan `(841 − 10) / 277 = 3` exacto ⇒ 3 columnas, no 4.
- **A4 horizontal, centrado:** cobertura 841 × 1340, holgura 0 × 151, origen `(0, −75.5)`. La hoja A1 cubre
  en el espacio de vista `[0, −75.5, 287, 124.5]` y la G1 `[0, 1064.5, 287, 1264.5]`. Rango de
  desplazamiento: x `[0, 0]`, y `[−75.5, 75.5]`.

**C.2 Una sola hoja (`fit_sheet`)**

| Papel | Orientación elegida | Escala | Tamaño impreso |
|---|---|---|---|
| A4 | vertical (horizontal daría 16.82 %) | 23.78 % (200/841) | 200.0 × 282.8 |
| A3 | vertical | 34.13 % (287/841) | 287.0 × 405.8 |
| Carta | vertical | 22.66 % (269.4/1189) | 190.6 × 269.4 |

**C.3 Rejilla fija (`fit_grid`)**

| Caso | Escala | Tamaño final |
|---|---|---|
| 2 × 2 A3 vertical (horizontal daría 47.43 %) | 67.06 % (564/841) | 564.0 × 797.4 |
| 2 × 2 A4 vertical | 46.37 % (390/841) | 390.0 × 551.4 |

**C.4 Escala de dibujo**

1:50 → 1:100 (`s = 0.5`): `T = 420.5 × 594.5`. En A4 horizontal: 2 × 4 = **8** hojas ← auto. En A4
vertical: 3 × 3 = 9.

**C.5 Sugerencias de ahorro** (A4, 100 % → horizontal 3 × 7 = 21)

| Escala | Rejilla | Hojas |
|---|---|---|
| 96.72 % (1150/1189) | horizontal 3 × 6 | 18 |
| 91.56 % (770/841) | vertical 4 × 4 | 16 |
| 80.74 % (960/1189) | horizontal 3 × 5 | 15 |

**C.6 PPP efectivos**

- 9933 × 14043 px como A0 → 300 PPP al 100 %.
- 4000 × 5657 px como A0 → 120.8 PPP al 100 % (`LOW_DPI`). En `fit_sheet` A4 → 508 PPP, y se reduce a
  `max_dpi` al embeber.

## Apéndice D — Textos de montaje e impresión

**Instrucciones de impresión** (pantalla de resultado y plano de montaje):

1. Imprime **a tamaño real / escala 100 %**. Desactiva "Ajustar a página", "Ajustar al área imprimible" o
   "Reducir páginas grandes". No uses el modo "Póster" del visor.
2. Elige el papel **{papel}** e impresión **a una cara**.
3. Imprime primero el plano de montaje y mide el cuadro de prueba: debe medir **50 mm** (o 2 in). Si no,
   revisa el punto 1 o calibra la impresora.

**Montaje con superposición:**

1. Ordena las hojas según el plano de montaje (A1 arriba a la izquierda).
2. En cada hoja, recorta el margen por la línea discontinua de los bordes **izquierdo y superior**
   (excepto en la primera columna y la primera fila).
3. Une cada fila de izquierda a derecha: coloca cada hoja sobre la franja derecha de la anterior,
   alineando su borde con la línea de alineación (▶), y pega.
4. Une las filas de arriba abajo de la misma forma, sobre la franja inferior.

**Montaje a tope:** recorta todos los bordes interiores por la línea discontinua y junta las hojas
haciendo coincidir los medios rombos. Pega con cinta por detrás.

La ayuda (`/help`) incluirá capturas verificadas de cómo imprimir al 100 % en Chrome, Edge, Firefox, Adobe
Acrobat Reader y Vista Previa de macOS.

## Apéndice E — Firmas de detección de formato

| Formato | Firma |
|---|---|
| PDF | `%PDF-` en los primeros 1024 bytes |
| PNG | `89 50 4E 47 0D 0A 1A 0A` |
| JPEG | `FF D8 FF` |
| TIFF | `49 49 2A 00`, `4D 4D 00 2A` (BigTIFF: `…2B…`) |
| GIF | `GIF87a` / `GIF89a` |
| BMP | `BM` |
| WebP | `RIFF????WEBP` |
| HEIF/AVIF | caja `ftyp` (offset 4) con marca `heic`, `heix`, `mif1`, `msf1` o `avif` |
| JPEG 2000 | `00 00 00 0C 6A 50 20 20` (JP2) / `FF 4F FF 51` (J2K) |
| PostScript / EPS | `%!PS` (EPS: `EPSF` en la 1ª línea); EPS DOS binario `C5 D0 D3 C6` |
| AI | Como PDF (si es compatible) o PostScript con `%%Creator: Adobe Illustrator` |
| SVG | Texto o XML con raíz `<svg` en los primeros 4 KB; `svgz` = gzip `1F 8B` que contiene SVG |
| OOXML | ZIP `50 4B 03 04` con `[Content_Types].xml` y parte principal `word/`, `xl/`, `ppt/` o `visio/` |
| ODF | ZIP cuya primera entrada `mimetype` es `application/vnd.oasis.opendocument.*` |
| OLE2 (doc, xls, ppt, vsd, pub) | `D0 CF 11 E0 A1 B1 1A E1` + nombres de stream para distinguirlos |
| RTF | `{\rtf` |
| EMF | registro 1 al inicio y `" EMF"` (`20 45 4D 46`) en el offset 40 |
| WMF | placeable `D7 CD C6 9A`, o cabecera `01 00 09 00` / `02 00 09 00` |
| XPS / OXPS | ZIP con `FixedDocumentSequence.fdseq` (o relación `fixedrepresentation`) |
| DWG | `AC10` + versión (`AC1015`, `AC1018`, `AC1024`, `AC1027`, `AC1032`…) |
| DXF | ASCII: grupo `0` / `SECTION` al inicio; binario: `AutoCAD Binary DXF` |
| CDR | `RIFF????CDR` (antiguos) o ZIP con `content/riffData.cdr` |
| HPGL | Texto con comandos `IN;`, `SP`, `PU`, `PD` al inicio (más extensión plt, hpgl o hpg) |
| DjVu | `AT&TFORM` |

Las firmas débiles (HPGL, DXF ASCII, SVG) solo se aceptan si también coincide la extensión o si el análisis
del contenido es inequívoco.
