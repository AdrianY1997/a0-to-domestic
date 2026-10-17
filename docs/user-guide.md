# Guía de usuario

Esta guía explica cómo convertir un plano o cartel grande en hojas de tamaño normal (A4, Carta, A3) para
imprimir en tu impresora, recortarlas y pegarlas.

> **Nota para quien mantiene esta guía:** las capturas y los nombres exactos de botones y opciones deben
> verificarse contra la versión final de la aplicación. Los nombres de este texto son aproximados.

## 1. Qué hace la aplicación

Un A0 (841 × 1189 mm) no cabe en una impresora doméstica. La aplicación lo divide en hojas normales que
puedes imprimir en casa. Puedes elegir:

- **Tamaño real o a escala** (100 %, 1:50 → 1:100, etc.): las hojas se pegan formando el plano completo.
- **Una sola hoja**: el documento entero reducido para revisarlo.
- **Una rejilla fija**: por ejemplo, un póster en 2 × 2 hojas A3.

Todo se procesa en tu propio equipo. No se envía nada a Internet.

## 2. Subir un documento

1. Abre la aplicación en el navegador (por defecto, http://localhost:8080).
2. Arrastra el archivo a la zona de subida, o pulsa para elegirlo.
3. Puedes subir varios archivos a la vez; cada uno se trata como un documento separado.
4. Espera a que termine el procesamiento. Una barra de progreso muestra el avance.

Formatos admitidos: PDF (el más recomendable), imágenes (JPG, PNG, TIFF, HEIC…), y según la versión instalada,
DWG, DXF, Office, SVG, HPGL, DjVu y otros. La lista completa está en el [README](../README.md#formatos-admitidos).

**Consejo:** un PDF exportado desde el programa de dibujo da mejores resultados que un DWG o un Office.

### Documentos con contraseña

Si el PDF tiene contraseña de apertura, la aplicación la pedirá. Si la contraseña es incorrecta, se muestra
un aviso y puedes intentarlo de nuevo. Si el PDF prohíbe la impresión, la aplicación no lo procesa: no puede
saltarse esa restricción.

### Varias páginas

Si el documento tiene varias páginas, verás la lista de páginas con su tamaño. Elige cuáles convertir
(todas, o una selección como `1,3-5`).

## 3. El editor

El editor muestra la página con la rejilla de hojas superpuesta y un panel de ajustes. Cada cambio se
recalcula al momento.

### 3.1 Papel e impresora

- **Papel:** A4, Carta, A3, Legal, A5 y otros, o un tamaño personalizado.
- **Orientación:** automática (la que necesita menos hojas), vertical u horizontal.
- **Impresora:** elige el perfil de tu impresora (ver el apartado 10). El perfil "Genérica (5 mm)" sirve
  como punto de partida.

El papel y las unidades que aparecen por defecto dependen de la región de tu navegador: Carta y pulgadas en
Estados Unidos, Canadá y varios países de Latinoamérica; A4 y milímetros en el resto. Puedes cambiarlos.

### 3.2 Escala

| Opción | Qué hace |
|---|---|
| **100 %** | Tamaño real. Es la opción para patrones de costura, plantillas y planos para replantear. |
| **Escala de dibujo** | Por ejemplo, 1:50 → 1:100: el plano se imprime a la mitad de su tamaño. |
| **Tamaño final** | Indicas el ancho o el alto que quieres obtener en papel. |
| **Una hoja** | El documento entero cabe en una sola hoja. |
| **Rejilla** | Número fijo de hojas en columnas y filas (p. ej. 2 × 2 A3). |
| **Máximo de hojas** | La aplicación elige la escala más grande que no supere ese número de hojas. |

Debajo del panel verás un resumen: número de hojas, papel, orientación, escala y tamaño final. Cuando una
escala ligeramente menor permitiría usar menos hojas, aparecen **alternativas** y **sugerencias de ahorro**.
Si estás haciendo un patrón a tamaño real, esas sugerencias no aparecen, porque cambiarían la escala.

### 3.3 Solapes y uniones

- **Superposición** (por defecto): las hojas se solapan una franja de 3 a 50 mm, con 10 mm por defecto. La
  hoja de encima tiene una línea discontinua de corte. Al montar, recortas por esa línea y pegas.
- **A tope**: las hojas se cortan en el borde y se unen. Cada junta tiene medios rombos que, al juntar las
  hojas, forman un rombo completo. Se usa mucho en patronaje.

### 3.4 Marcas

- **Líneas de corte**: dónde recortar.
- **Alineación**: línea y triángulos (▶) que indican dónde debe caer el borde de la hoja vecina.
- **Etiquetas**: el identificador de cada hoja (A1, B3…), su número de orden y los vecinos (↑ ↓ ← →).
- **Regla de 50 mm**: para comprobar la escala a simple vista.
- **Plano de montaje**: una página extra con el mapa de hojas, el resumen del trabajo y las instrucciones.
- **Cuadro de prueba**: un cuadrado de 50 × 50 mm (y de 2 × 2 pulgadas). Ver el apartado 8.

Las marcas se dibujan dentro del área imprimible. Con la superposición, la mayoría quedan ocultas al pegar.

### 3.5 Hojas vacías

Por defecto, las hojas sin contenido no se imprimen (ahorras papel y tinta). El plano de montaje las marca
con una cruz para que sepas que no faltan. La **sensibilidad** (estricta, normal o tolerante) decide qué se
considera vacío: usa "tolerante" si el fondo del escaneo tiene manchas.

### 3.6 Región, recorte y rotación

- **Región:** dibuja un rectángulo sobre la vista previa, o escribe sus medidas, para imprimir solo un detalle a
  tamaño real.
- **Recorte automático:** quita los márgenes blancos alrededor del dibujo.
- **Rotación:** 0°, 90°, 180° o 270°.

### 3.7 Elegir hojas concretas

Puedes desmarcar hojas para no imprimirlas, o escribir una lista como `A1, B3-B5`. Es útil para reimprimir
solo una hoja que se atascó o se manchó, sin generar todo otra vez.

### 3.8 Vista de hoja

Al hacer doble clic en una hoja, ves cómo quedará con sus marcas, etiquetas y vecinos.

## 4. Opciones de planos CAD (DXF, DWG)

Los archivos de CAD no tienen un tamaño de papel fijo. Al subirlos, la aplicación te pide:

- **Presentación (layout):** el espacio de papel del programa (Presentación1, etc.) o el Modelo.
- **Unidades:** si el archivo no indica sus unidades, se asumen milímetros y se marca como "asumido".
- **Escala** (en el espacio Modelo): la escala del dibujo, p. ej. 1:50.
- **Color:** en color, en blanco y negro, o en gris.

Si puedes, exporta el plano a PDF desde el programa de CAD: el resultado suele ser más fiable.

## 5. Capas

Si el PDF tiene capas (por ejemplo, tallas en un patrón o instalaciones en un plano), aparecen en un panel con
su estado. Puedes ocultar o mostrar cada una. Lo que ves en la vista previa es lo que sale impreso.

Algunas capas vienen apagadas en el archivo original. Siguen apagadas por defecto, pero puedes encenderlas en
el panel.

## 6. Modos de color

| Modo | Cuándo usarlo |
|---|---|
| **Color** | Por defecto. Conserva los colores del original. |
| **Escala de grises** | Ahorra tinta de color; conserva los vectores. |
| **Blanco y negro** | Planos en color que quieres en negro. El **umbral** (0–255, 160 por defecto) decide qué se vuelve negro; con un umbral alto, todo lo que no es blanco se vuelve negro. Rasteriza la salida. |
| **Invertir** | Para dibujos con fondo oscuro. |
| **Blanquear fondo** | Para escaneos amarillentos o con fondo no blanco. |

Las opciones **Rasterizar** y **Blanco y negro** convierten la salida en imágenes. Si una impresora se atasca
con un PDF complejo, rasterizar la salida suele resolverlo. Otras salidas útiles: un ZIP con una imagen por
hoja (PNG, JPEG o TIFF) y una copia reducida del documento en una sola hoja.

## 7. Generar y descargar

1. Revisa el resumen y los avisos del panel izquierdo.
2. Pulsa **Generar**. Aparece el progreso.
3. Cuando termine, verás las miniaturas de las páginas y los botones de **descarga**.

Los trabajos y documentos caducan tras un tiempo (por defecto, 72 horas). Descarga los archivos antes de que
caduquen, o consulta el historial mientras sigan disponibles.

## 8. Imprimir

### 8.1 Imprimir al 100 %: lo más importante

El PDF tiene el tamaño exacto de cada hoja. Si el visor reduce o amplía la página, las medidas se
estropean y las hojas no encajan.

En el cuadro de diálogo de impresión de tu visor, busca las opciones de escala y elige **Tamaño real**,
**100 %** o **Sin ajustar**. Desactiva cualquier opción de **Ajustar a página**, **Ajustar al área imprimible**
o **Reducir páginas grandes**. No uses el modo "Póster" del visor.

Los nombres cambian según el programa; esto es lo que suele aparecer:

- **Chrome y Edge:** en "Tamaño" o "Escala", elige "Tamaño real" o "100 %"; desactiva "Ajustar a página".
- **Firefox:** en "Escala", elige "100 %" o "Tamaño real".
- **Adobe Acrobat Reader:** en "Tamaño de página y gestión", elige "Tamaño real" o "Ninguno".
- **Vista Previa de macOS:** en "Escala", elige "100 %" y desactiva "Ajustar al papel".

En todos los casos:

1. Elige el papel indicado (A4, Carta…).
2. Imprime a **una cara**.
3. Imprime primero el **plano de montaje** (ver 8.2).

### 8.2 Cuadro de prueba

El plano de montaje incluye un cuadrado de **50 mm**. Mídelo con una regla:

- Si mide **50 mm** (o 2 pulgadas), la escala es correcta: imprime el resto.
- Si no mide 50 mm, revisa las opciones de escala de tu visor. Si sigue mal, calibra la impresora (apartado 10).

### 8.3 Imprimir directamente (IPP)

Si tu impresora admite IPP (la mayoría de las modernas, y AirPrint), puedes enviarle el trabajo desde la
aplicación sin pasar por el visor. Introduce la dirección de la impresora (por ejemplo, `ipp://192.168.1.50/ipp/print`).
La aplicación envía el trabajo con escala 100 % y sin ajustes de página. Un administrador debe activar esta
función (ver el apartado 13).

## 9. Montaje

Hay dos formas de montar las hojas.

### 9.1 Con superposición

1. Ordena las hojas según el plano de montaje (A1 arriba a la izquierda).
2. En cada hoja, recorta el margen por la línea discontinua de los bordes **izquierdo y superior**. No recortes
   en la primera columna ni en la primera fila.
3. Une cada fila de izquierda a derecha: coloca cada hoja sobre la franja derecha de la anterior, alineando su
   borde con la línea de alineación (▶), y pega.
4. Une las filas de arriba abajo de la misma forma, sobre la franja inferior.

### 9.2 A tope

Recorta todos los bordes interiores por la línea discontinua. Junta las hojas haciendo coincidir los medios
rombos. Pega con cinta por detrás.

**Consejo:** pega primero con cinta por el reverso y comprueba que el conjunto está recto antes de hacerlo
definitivo.

## 10. Impresoras y calibración

### 10.1 Perfiles de impresora

Un perfil guarda los márgenes reales de tu impresora (la franja que no puede imprimir), los papeles que
admite y una corrección de escala. Hay cuatro perfiles integrados:

- **Genérica (5 mm):** margen de 5 mm en todos los lados. Punto de partida.
- **Inyección típica:** márgenes de 3 / 3 / 5 / 3 mm.
- **Láser típica:** márgenes de 4.5 mm.
- **Sin bordes:** sin margen, para impresoras que imprimen a sangre.

Los perfiles integrados son de solo lectura. Para cambiar algo, duplícalo y edita la copia.

### 10.2 Calibrar la impresora

Calibrar corrige el margen real y la escala de tu impresora. Se hace una vez y se reutiliza.

1. **Imprime** la página de calibración para el papel elegido. Tiene reglas, cuadros de 50 mm y 2 pulgadas, y
   marcos cada 0.5 mm que te dejan leer los márgenes reales de cada lado.
2. **Mide** las reglas y los cuadros con una regla de precisión.
3. **Introduce** las medidas y los márgenes en el asistente.
4. **Guarda**: el perfil queda corregido y las hojas nuevas usan la corrección.

Si la corrección sale fuera de lo razonable (más de un 3 % de diferencia), el asistente no guarda y avisa: casi
siempre significa que la página se imprimió con "Ajustar a página". Vuelve a imprimir con escala 100 %.

## 11. Presets

Un preset es un conjunto de ajustes con nombre, p. ej. "Patrón de costura 1:1 (A4, a tope)".

- **Integrados:** Tamaño real en A4, Carta o A3; Reducido a una A4 o A3; Póster 2 × 2 A3; Patrón de costura 1:1.
- **Tuyos:** crea un preset desde los ajustes actuales, edítalo, bórralo, o expórtalo e impórtalo como archivo JSON.

## 12. Historial

El historial muestra los documentos y trabajos recientes. Desde ahí puedes reabrir un documento, regenerar un
trabajo con los mismos ajustes o descargar sus archivos, mientras no hayan caducado. El botón **Borrar ahora**
elimina los archivos antes de la caducidad.

## 13. Para administradores

Estas funciones se configuran con variables de entorno en `compose.yml` (lista completa en
[SPEC.md §12.4](../SPEC.md#124-variables-de-entorno)).

- **Acceso desde otros equipos de la red:** activa la contraseña con `A0TD_AUTH_MODE=password`. Quita
  `127.0.0.1` del puerto. Ver el [README](../README.md#seguridad).
- **Impresión directa (IPP):** `A0TD_ENABLE_IPP=true`.
- **Carpeta vigilada:** con `A0TD_HOTFOLDER=true`, los archivos que se copian a `inbox/<preset>/` se procesan
  con ese preset y el resultado aparece en `outbox/<preset>/`. Los originales pasan a `.done/` o `.error/`.
  La carpeta se revisa cada pocos segundos (`A0TD_HOTFOLDER_INTERVAL_S`).
- **Fuentes propias:** los archivos de fuentes en `/data/fonts` están disponibles para LibreOffice e Inkscape
  (documentos de Office, SVG, metarchivos). Sin ellas, algunas fuentes se sustituyen y la aplicación lo avisa.
- **Idioma:** español por defecto; `A0TD_DEFAULT_LOCALE=en` para inglés.

## 14. Preguntas frecuentes y problemas

**La medida no es correcta (el cuadro de 50 mm no mide 50 mm).**
El visor está reduciendo o ampliando la página. Imprime con "Tamaño real" o "100 %" y sin "Ajustar a página".
Si sigue, calibra la impresora.

**Faltan franjas en los bordes de cada hoja.**
Tu impresora tiene márgenes mayores que los del perfil. Calibra la impresora o usa un perfil con márgenes
mayores. Si el margen es muy grande, deja más solape (p. ej. 20 mm) para que las hojas se cubran.

**Aviso `LOW_DPI` (o `VERY_LOW_DPI`).**
La imagen tiene poca resolución para la escala elegida. Al imprimirla se verá pixelada o borrosa. Reduce la
escala o usa una hoja más grande. Con 100 % y una foto pequeña, el aviso es normal; con `fit_sheet` en A4
desaparece.

**Aviso `PHYSICAL_SIZE_ASSUMED`.**
La imagen no indica su tamaño físico (o es inverosímil). Se asume A0 en la orientación de la imagen. Si no es
así, indica el tamaño real: por papel, por ancho, por alto o por resolución (PPP).

**El PDF pide contraseña y no la acepto.**
Comprueba mayúsculas y minúsculas. Si el PDF prohíbe imprimir, la aplicación no puede procesarlo.

**Aviso `FONTS_SUBSTITUTED`.**
Un documento de Office usa fuentes que no están instaladas. Se sustituyen por fuentes de métricas parecidas, así
que el texto puede variar un poco de posición. Lo más seguro es exportar el documento a PDF desde el programa
original.

**Error `UNSUPPORTED_FORMAT` o `FORMAT_NOT_AVAILABLE_IN_IMAGE`.**
El formato no se admite o la imagen instalada es la ligera (`slim`). Pide a quien administra la aplicación
la imagen `full`, o exporta el archivo a PDF.

**Error `CORRUPT_FILE` o `CONVERSION_FAILED`.**
El archivo está dañado o el programa de origen no lo exporta bien. Prueba a volver a exportarlo a PDF.

**Error `TOO_MANY_SHEETS`.**
La configuración necesita más hojas que el límite (500 por defecto). Usa una escala menor, un papel más grande
o una rejilla que cubra menos hojas.

**Aviso `MANY_SHEETS`.**
Son más de 50 hojas. Es un aviso informativo; comprueba que es lo que esperas.

**Aviso `SMALL_TEXT` o `THIN_LINES`.**
Al escalar, el texto queda por debajo de 5 pt o las líneas son más finas que 0.1 mm. Puede que no se lea o
desaparezca al imprimir. Aumenta la escala o revisa el original.

**La hoja no coincide con la vecina.**
Repite el cuadro de prueba. Si la escala es correcta, calibra la impresora (el margen real puede ser distinto).

**Quiero imprimir una sola hoja otra vez.**
Desmarca el resto en el editor (o escribe `A1` en la lista de hojas) y genera de nuevo.
