import pytest

from a0td.domain.papers import (
    PAPERS,
    SOURCE_SIZES,
    Orientation,
    PaperId,
    get_paper,
    match_paper,
    match_source_size,
    orientation_of,
    oriented,
)


def test_catalog_complete_and_portrait() -> None:
    assert set(PAPERS) == {p.value for p in PaperId} - {"CUSTOM"}
    assert all(p.width_mm <= p.height_mm for p in PAPERS.values())
    assert get_paper("LETTER").width_mm == pytest.approx(215.9)
    assert get_paper("A3PLUS").width_in == 13
    assert get_paper("A4").pwg_name == "iso_a4_210x297mm"
    with pytest.raises(KeyError):
        get_paper("NOPE")


def test_source_sizes_listed() -> None:
    ids = {s.id for s in SOURCE_SIZES}
    assert {"4A0", "A0", "B2", "ARCH_E1", "ANSI_C"} <= ids
    assert len(SOURCE_SIZES) == 17


@pytest.mark.parametrize(
    ("w", "h", "std", "landscape"),
    [
        (841, 1189, "A0", False),
        (1189, 841, "A0", True),
        (842.5, 1190.5, "A0", False),  # within 2 mm
        (845, 1193, "A0", False),  # within 0.5 % (4.2 mm)
        (594, 841, "A1", False),
        (914.4, 1219.2, "ARCH_E", False),
        (863.6, 1117.6, "ANSI_E", False),
        (210, 297, "A4", False),
    ],
)
def test_match_source(w: float, h: float, std: str, landscape: bool) -> None:
    m = match_source_size(w, h)
    assert m is not None
    assert m.standard.id == std
    assert m.landscape is landscape


def test_match_source_none_when_far() -> None:
    assert match_source_size(900, 1300) is None
    assert match_source_size(841, 1200) is None


def test_match_prefers_nearest() -> None:
    m = match_source_size(762, 1066.8)
    assert m is not None
    assert m.standard.id == "ARCH_E1"
    assert m.deviation_mm == pytest.approx(0)


def test_match_paper() -> None:
    m = match_paper(279.4, 215.9)
    assert m is not None
    assert m.standard.id == "LETTER"
    assert m.landscape
    assert match_paper(300, 300) is None


def test_orientation_helpers() -> None:
    assert oriented(210, 297, Orientation.LANDSCAPE) == (297, 210)
    assert oriented(297, 210, Orientation.PORTRAIT) == (210, 297)
    assert oriented(297, 210, Orientation.AUTO) == (297, 210)
    assert orientation_of(210, 297) is Orientation.PORTRAIT
    assert orientation_of(297, 210) is Orientation.LANDSCAPE
