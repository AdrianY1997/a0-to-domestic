# 0008. Estado de las capas OCG

- Estado: aceptada
- Fecha: WP-18 (decisiones del orquestador tras la auditoría)

## Contexto

Los PDF de planos y patrones tienen capas (OCG) con tallas, instalaciones o anotaciones. El panel, la vista previa y
la salida deben mostrar lo mismo. Las capas apagadas en el origen deben seguir apagadas por defecto, aunque el
usuario quiera verlas. Además, al convertir a escala de grises con Ghostscript, las capas pueden desaparecer o
cambiar de referencia interna (xref).

## Decisión

- **Regla de estado:** una capa apagada en el origen sigue apagada por defecto. El campo
  `source.visible_layers` permite encenderla de forma explícita. `hidden_layers` apaga de forma explícita.
- **Coherencia:** panel, vista previa y salida usan la misma tabla de estado.
- **Identificación:** en escala de grises, las capas se identifican por **nombre**, nunca por xref de la copia que
  genera Ghostscript.
- **Fallback:** si Ghostscript elimina las capas, la salida se rasteriza y se avisa con `LAYERS_RASTERIZED`.
- **Capa de marcas:** se excluye de la ocultación, para que las marcas no desaparezcan con las capas del dibujo.
- **OCMD:** se respetan `AllOff` y `AnyOff`.
- **Reapertura:** MuPDF lee la configuración de capas solo al abrir el archivo, así que el rasterizado y la vista
  previa vuelven a abrir el PDF con las capas apagadas.
- Las estadísticas de legibilidad se acotan antes de materializar, y el grosor de línea se mide con la CTM aplicada.

## Alternativas consideradas

- **Encender todas las capas al abrir**: rompe la intención del diseñador y la coherencia con la vista previa.
- **Identificar por xref**: se rompe en cuanto Ghostscript reescribe el PDF.

## Consecuencias

- Un usuario que espera ver una capa apagada en el origen debe encenderla en el panel.
- El interruptor "Rasterizar salida" de la UI (SAL-04) aún no está en la interfaz; el grosor mínimo depende de él.
  Queda como pendiente documentado en PROGRESS.
