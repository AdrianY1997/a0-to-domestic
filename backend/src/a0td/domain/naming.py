"""Sheet naming (SPEC 5.7): row letters, ids for the three schemes, page prefix."""

from enum import StrEnum


class NamingScheme(StrEnum):
    LETTER_NUMBER = "letter_number"
    NUMBER_NUMBER = "number_number"
    SEQUENTIAL = "sequential"


class SheetOrder(StrEnum):
    ROWS = "rows"
    COLUMNS = "columns"


def row_letters(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA, 27 -> AB ... (bijective base 26)."""
    if index < 0:
        raise ValueError("row index must be >= 0")
    n = index + 1
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def row_index(letters: str) -> int:
    """Inverse of :func:`row_letters`."""
    if not letters or not letters.isascii() or not letters.isalpha():
        raise ValueError(f"invalid row letters: {letters!r}")
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def page_prefix(page_number: int | None, page_count: int = 2) -> str:
    """``P2-`` when the document has more than one page, otherwise empty (1-based page)."""
    if page_number is None or page_count <= 1:
        return ""
    return f"P{page_number}-"


def sheet_id(
    row: int,
    col: int,
    *,
    naming: NamingScheme = NamingScheme.LETTER_NUMBER,
    rows: int = 1,
    cols: int = 1,
    order: SheetOrder = SheetOrder.ROWS,
    page_number: int | None = None,
    page_count: int = 1,
) -> str:
    """Id of the sheet at 0-based ``(row, col)``.

    ``letter_number`` -> ``B3``; ``number_number`` -> ``2-3`` (row-col, 1-based);
    ``sequential`` -> position in the full grid in the chosen print order (1-based).
    """
    if naming is NamingScheme.LETTER_NUMBER:
        base = f"{row_letters(row)}{col + 1}"
    elif naming is NamingScheme.NUMBER_NUMBER:
        base = f"{row + 1}-{col + 1}"
    else:
        seq = row * cols + col + 1 if order is SheetOrder.ROWS else col * rows + row + 1
        base = str(seq)
    return page_prefix(page_number, page_count) + base
