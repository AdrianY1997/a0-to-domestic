"""Texts printed on the output (SPEC Appendix D), keyed by locale."""
# ruff: noqa: RUF001

from typing import Final

DEFAULT_LOCALE: Final = "es"

_ES: dict[str, str] = {
    "sheet": "Hoja",
    "assembly_title": "Plano de montaje",
    "sum_document": "Documento",
    "sum_page": "Página",
    "sum_paper": "Papel",
    "sum_scale": "Escala",
    "sum_grid": "Rejilla",
    "sum_sheets": "Hojas",
    "sum_final": "Tamaño final",
    "sum_join": "Unión",
    "sum_printer": "Impresora",
    "sum_generated": "Generado",
    "portrait": "vertical",
    "landscape": "horizontal",
    "sheets_detail": "{printed} impresas, {blank} vacías omitidas, {off} no seleccionadas",
    "join_overlap": "solape de {o:g} mm",
    "join_butt": "a tope",
    "correction": "corrección {kx:.4f} × {ky:.4f}",
    "test_square_50": "50 × 50 mm",
    "test_square_2in": "2 × 2 in",
    "ruler_100": "Regla de 100 mm",
    "test_caption": "Mide los cuadros: deben medir 50 mm y 2 in.",
    "skipped_hint": "Tachadas: no se imprimen",
    "print_title": "Instrucciones de impresión",
    "print_1": (
        "Imprime a tamaño real / escala 100 %. Desactiva «Ajustar a página», "
        "«Ajustar al área imprimible» o «Reducir páginas grandes». "
        "No uses el modo «Póster» del visor."
    ),
    "print_2": "Elige el papel {paper} e impresión a una cara.",
    "print_3": (
        "Imprime primero el plano de montaje y mide el cuadro de prueba: debe medir "
        "50 mm (o 2 in). Si no, revisa el punto 1 o calibra la impresora."
    ),
    "overlap_title": "Montaje con superposición",
    "overlap_1": "Ordena las hojas según el plano de montaje (A1 arriba a la izquierda).",
    "overlap_2": (
        "En cada hoja, recorta el margen por la línea discontinua de los bordes izquierdo y "
        "superior (excepto en la primera columna y la primera fila)."
    ),
    "overlap_3": (
        "Une cada fila de izquierda a derecha: coloca cada hoja sobre la franja derecha de la "
        "anterior, alineando su borde con la línea de alineación (triángulos), y pega."
    ),
    "overlap_4": "Une las filas de arriba abajo de la misma forma, sobre la franja inferior.",
    "butt_title": "Montaje a tope",
    "butt_1": (
        "Recorta todos los bordes interiores por la línea discontinua y junta las hojas "
        "haciendo coincidir los medios rombos. Pega con cinta por detrás."
    ),
}

_EN: dict[str, str] = {
    "sheet": "Sheet",
    "assembly_title": "Assembly plan",
    "sum_document": "Document",
    "sum_page": "Page",
    "sum_paper": "Paper",
    "sum_scale": "Scale",
    "sum_grid": "Grid",
    "sum_sheets": "Sheets",
    "sum_final": "Final size",
    "sum_join": "Join",
    "sum_printer": "Printer",
    "sum_generated": "Generated",
    "portrait": "portrait",
    "landscape": "landscape",
    "sheets_detail": "{printed} printed, {blank} blank skipped, {off} not selected",
    "join_overlap": "overlap of {o:g} mm",
    "join_butt": "butt",
    "correction": "correction {kx:.4f} × {ky:.4f}",
    "test_square_50": "50 × 50 mm",
    "test_square_2in": "2 × 2 in",
    "ruler_100": "100 mm ruler",
    "test_caption": "Measure the squares: they must be 50 mm and 2 in.",
    "skipped_hint": "Crossed out: not printed",
    "print_title": "Printing instructions",
    "print_1": (
        'Print at actual size / 100 % scale. Turn off "Fit to page", "Fit to printable '
        'area" and "Shrink oversized pages". Do not use the viewer\'s "Poster" mode.'
    ),
    "print_2": "Choose paper {paper} and one-sided printing.",
    "print_3": (
        "Print the assembly plan first and measure the test square: it must be 50 mm "
        "(or 2 in). If not, check step 1 or calibrate the printer."
    ),
    "overlap_title": "Assembly with overlap",
    "overlap_1": "Arrange the sheets as in the assembly plan (A1 at the top left).",
    "overlap_2": (
        "On each sheet, trim the margin along the dashed line of the left and top edges "
        "(except in the first column and the first row)."
    ),
    "overlap_3": (
        "Join each row from left to right: place each sheet over the right strip of the "
        "previous one, aligning its edge with the alignment line (triangles), and glue."
    ),
    "overlap_4": "Join the rows from top to bottom the same way, over the bottom strip.",
    "butt_title": "Butt-joined assembly",
    "butt_1": (
        "Trim every interior edge along the dashed line and bring the sheets together "
        "matching the half diamonds. Tape them from behind."
    ),
}

STRINGS: Final[dict[str, dict[str, str]]] = {"es": _ES, "en": _EN}


def get_strings(locale: str) -> dict[str, str]:
    """Texts for ``locale`` (``es-MX`` -> ``es``); unknown locales fall back to Spanish."""
    return STRINGS.get(locale.split("-")[0].lower(), STRINGS[DEFAULT_LOCALE])
