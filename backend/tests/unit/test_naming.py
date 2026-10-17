import pytest

from a0td.domain.naming import (
    NamingScheme,
    SheetOrder,
    page_prefix,
    row_index,
    row_letters,
    sheet_id,
)


@pytest.mark.parametrize(
    ("i", "s"),
    [(0, "A"), (1, "B"), (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ"), (52, "BA"), (701, "ZZ"),
     (702, "AAA")],
)  # fmt: skip
def test_row_letters_roundtrip(i: int, s: str) -> None:
    assert row_letters(i) == s
    assert row_index(s) == i


def test_row_letters_invalid() -> None:
    with pytest.raises(ValueError, match="row index"):
        row_letters(-1)
    with pytest.raises(ValueError, match="invalid row letters"):
        row_index("A1")


def test_ids_three_schemes() -> None:
    assert sheet_id(1, 2) == "B3"
    assert sheet_id(1, 2, naming=NamingScheme.NUMBER_NUMBER) == "2-3"
    kw = {"naming": NamingScheme.SEQUENTIAL, "rows": 3, "cols": 4}
    assert sheet_id(1, 2, **kw, order=SheetOrder.ROWS) == "7"  # type: ignore[arg-type]
    assert sheet_id(1, 2, **kw, order=SheetOrder.COLUMNS) == "8"  # type: ignore[arg-type]
    assert sheet_id(0, 0, **kw) == "1"  # type: ignore[arg-type]


def test_page_prefix() -> None:
    assert page_prefix(2, 3) == "P2-"
    assert page_prefix(1, 1) == ""
    assert page_prefix(None, 3) == ""
    assert sheet_id(0, 0, page_number=2, page_count=3) == "P2-A1"
    assert sheet_id(0, 0, page_number=1, page_count=1) == "A1"
