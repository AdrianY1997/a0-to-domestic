# 0009. Exactitud del tamaño de página en CAD

- Estado: aceptada
- Fecha: WP-17 (corrección tras la auditoría)

## Contexto

Un plano DXF en espacio modelo se dibuja con ezdxf sobre PyMuPDF. El backend de PyMuPDF redondea el tamaño de la
página. Un error de centésimas de milímetro en la página se multiplica al imprimir y rompe el criterio de precisión
(≤ 0.05 mm, O2). Ejemplo: una línea de 1000 mm a 1:50 debe medir 20.00 mm en papel.

## Decisión

- Tras generar el PDF con ezdxf, se **reescribe el MediaBox y el CropBox con los valores exactos** calculados desde
  el layout o la escala.
- El contenido se compensa con una matriz `cm` para que la posición relativa no cambie.
- La tolerancia de la prueba es 0.05 mm. No se relaja para que pase un test: si una librería redondea, se corrige el
  tamaño después.

## Decisiones relacionadas tomadas en la misma auditoría

- **Sin deduplicación para DXF y DWG.** El mismo archivo con distintas opciones da un canónico distinto. Por eso la
  deduplicación por SHA-256 (ING-10) se omite cuando se suben opciones CAD.
- **`cad-options` no destruye el canónico.** El nuevo canónico se genera en un directorio temporal y solo sustituye al
  anterior si termina bien.
- **Unidades asumidas.** Un DXF sin unidades se toma como mm y se marca con `PHYSICAL_SIZE_ASSUMED`
  (`reason: cad_units`).

## Alternativas consideradas

- **Aceptar el redondeo**: el error se acumula en el montaje y viola O2.
- **Cambiar de backend de PDF**: exigiría reescribir el conversor de CAD.

## Consecuencias

- Hay una prueba de exactitud con una línea de 1000 mm a 1:50 (20.00 mm).
- Si cambia la versión de ezdxf o de PyMuPDF, esta corrección debe revisarse.
