# 0005. Imposición vectorial con un XObject compartido

- Estado: aceptada
- Fecha: 2026-10-15 (SPEC §9.7, SAL-01); WP-07

## Contexto

Un A0 vectorial dividido en 21 hojas no debe multiplicar su tamaño por 21. Además, cada hoja debe medir exactamente
el papel y la escala debe ser exacta (≤ 0.05 mm, O2). Para un origen raster, cada hoja solo debe embeber su recorte.

## Decisión

- **Motor vectorial:** `page.show_pdf_page(rect_destino, canonical, pno, clip=src_rect ∩ página)`. El rectángulo
  de destino se calcula para la intersección, de modo que la escala sea exactamente `s`. PyMuPDF reutiliza el mismo
  XObject en todas las teselas, así que el tamaño no crece con el número de hojas.
- **Motor raster:** pyvips `extract_area` del rectángulo de la tesela, redondeado hacia fuera a píxeles. Se reescala
  solo si los PPP efectivos superan `max_dpi` (por defecto 600), y se inserta en el rectángulo exacto que corresponde a
  los píxeles redondeados.
- **Matrices:** se construyen a mano desde `page.cropbox` y la altura de la MediaBox. `page.transformation_matrix`
  pierde el desplazamiento de la CropBox cuando la página está rotada; por eso hay pruebas que combinan rotación y
  CropBox desplazada.
- **Salida:** las marcas y el plano de montaje se dibujan con primitivas vectoriales (Helvetica base-14, sin incrustar).
  El PDF no copia JavaScript, acciones, adjuntos ni anotaciones del origen.

## Alternativas consideradas

- **Rasterizar todo el origen**: pierde nitidez y dispara el tamaño de salida.
- **Dibujar cada tesela con su propio contenido**: multiplica el tamaño del PDF.

## Consecuencias

- Prueba de reensamblado (SPEC §15.4): las hojas renderizadas a 150 PPP se componen y se comparan con el render
  directo a escala `s`, con tolerancia de 1 px fuera de las costuras.
- El PDF vectorial pasa de ≤ 1.2 × el origen + 200 KB (criterio de tamaño).
- Cualquier cambio en la matriz exige revisar también la rotación y la CropBox desplazada.
