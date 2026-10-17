"""Paper catalog (SPEC Appendix A), recognized source sizes and nearest-standard matching."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from a0td.domain.units import in_to_mm

CUSTOM_MIN_MM = (50.0, 50.0)
CUSTOM_MAX_MM = (500.0, 1500.0)

# Tolerance for matching a measured size to a standard one: the larger of both.
MATCH_ABS_TOL_MM = 2.0
MATCH_REL_TOL = 0.005


class Orientation(StrEnum):
    AUTO = "auto"
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class PaperId(StrEnum):
    A3 = "A3"
    A3PLUS = "A3PLUS"
    A4 = "A4"
    A5 = "A5"
    A6 = "A6"
    B4 = "B4"
    B5 = "B5"
    JIS_B4 = "JIS_B4"
    JIS_B5 = "JIS_B5"
    LETTER = "LETTER"
    LEGAL = "LEGAL"
    OFICIO_MX = "OFICIO_MX"
    FOLIO = "FOLIO"
    TABLOID = "TABLOID"
    EXECUTIVE = "EXECUTIVE"
    STATEMENT = "STATEMENT"
    PHOTO_10X15 = "PHOTO_10X15"
    PHOTO_13X18 = "PHOTO_13X18"
    PHOTO_20X25 = "PHOTO_20X25"
    CUSTOM = "CUSTOM"


@dataclass(frozen=True, slots=True)
class Paper:
    """An output paper, portrait (``width_mm <= height_mm``)."""

    id: str
    name: str
    width_mm: float
    height_mm: float
    usage: str = ""
    width_in: float | None = None
    height_in: float | None = None
    pwg_name: str | None = None


@dataclass(frozen=True, slots=True)
class SourceSize:
    """A recognized source (drawing) size, portrait."""

    id: str
    name: str
    width_mm: float
    height_mm: float


def _p(
    id_: PaperId,
    name: str,
    w: float,
    h: float,
    usage: str,
    pwg: str,
    inches: tuple[float, float] | None = None,
) -> Paper:
    win, hin = inches if inches else (None, None)
    return Paper(id_.value, name, w, h, usage, win, hin, pwg)


_CATALOG: tuple[Paper, ...] = (
    _p(PaperId.A3, "A3", 297, 420, "ISO", "iso_a3_297x420mm"),
    _p(
        PaperId.A3PLUS,
        "A3+ / Super B",
        329,
        483,
        "Large photo",
        "na_super-b_13x19in",
        (13, 19),
    ),
    _p(PaperId.A4, "A4", 210, 297, "ISO", "iso_a4_210x297mm"),
    _p(PaperId.A5, "A5", 148, 210, "ISO", "iso_a5_148x210mm"),
    _p(PaperId.A6, "A6", 105, 148, "ISO", "iso_a6_105x148mm"),
    _p(PaperId.B4, "B4 (ISO)", 250, 353, "ISO", "iso_b4_250x353mm"),
    _p(PaperId.B5, "B5 (ISO)", 176, 250, "ISO", "iso_b5_176x250mm"),
    _p(PaperId.JIS_B4, "B4 (JIS)", 257, 364, "Japan", "jis_b4_257x364mm"),
    _p(PaperId.JIS_B5, "B5 (JIS)", 182, 257, "Japan", "jis_b5_182x257mm"),
    _p(
        PaperId.LETTER,
        "Carta / Letter",
        215.9,
        279.4,
        "North and Latin America",
        "na_letter_8.5x11in",
        (8.5, 11),
    ),
    _p(
        PaperId.LEGAL,
        "Legal / Oficio US",
        215.9,
        355.6,
        "North and Latin America",
        "na_legal_8.5x14in",
        (8.5, 14),
    ),
    _p(
        PaperId.OFICIO_MX,
        "Oficio (Mexico)",
        216,
        340,
        "Mexico",
        "om_oficio_216x340mm",
        (8.5, 13.4),
    ),
    _p(
        PaperId.FOLIO,
        "Folio / Oficio 8.5x13",
        215.9,
        330.2,
        "Latin America, Philippines",
        "na_foolscap_8.5x13in",
        (8.5, 13),
    ),
    _p(
        PaperId.TABLOID,
        "Tabloide / Ledger",
        279.4,
        431.8,
        "North America",
        "na_ledger_11x17in",
        (11, 17),
    ),
    _p(
        PaperId.EXECUTIVE,
        "Ejecutivo",
        184.15,
        266.7,
        "North America",
        "na_executive_7.25x10.5in",
        (7.25, 10.5),
    ),
    _p(
        PaperId.STATEMENT,
        "Media carta / Statement",
        139.7,
        215.9,
        "North America",
        "na_invoice_5.5x8.5in",
        (5.5, 8.5),
    ),
    _p(
        PaperId.PHOTO_10X15,
        "Foto 10x15 (4x6 in)",
        101.6,
        152.4,
        "Photo",
        "na_index-4x6_4x6in",
        (4, 6),
    ),
    _p(
        PaperId.PHOTO_13X18,
        "Foto 13x18 (5x7 in)",
        127,
        177.8,
        "Photo",
        "na_5x7_5x7in",
        (5, 7),
    ),
    _p(
        PaperId.PHOTO_20X25,
        "Foto 20x25 (8x10 in)",
        203.2,
        254,
        "Photo",
        "na_govt-letter_8x10in",
        (8, 10),
    ),
)

PAPERS: dict[str, Paper] = {p.id: p for p in _CATALOG}

SOURCE_SIZES: tuple[SourceSize, ...] = (
    SourceSize("4A0", "4A0", 1682, 2378),
    SourceSize("2A0", "2A0", 1189, 1682),
    SourceSize("A0", "A0", 841, 1189),
    SourceSize("A1", "A1", 594, 841),
    SourceSize("A2", "A2", 420, 594),
    SourceSize("A3", "A3", 297, 420),
    SourceSize("A4", "A4", 210, 297),
    SourceSize("B0", "B0", 1000, 1414),
    SourceSize("B1", "B1", 707, 1000),
    SourceSize("B2", "B2", 500, 707),
    SourceSize("ARCH_E", "ARCH E", 914.4, 1219.2),
    SourceSize("ARCH_E1", "ARCH E1", 762, 1066.8),
    SourceSize("ARCH_D", "ARCH D", 609.6, 914.4),
    SourceSize("ARCH_C", "ARCH C", 457.2, 609.6),
    SourceSize("ANSI_E", "ANSI E", 863.6, 1117.6),
    SourceSize("ANSI_D", "ANSI D", 558.8, 863.6),
    SourceSize("ANSI_C", "ANSI C", 431.8, 558.8),
)


def get_paper(paper_id: str) -> Paper:
    """Look up a catalog paper (``CUSTOM`` has no fixed size and is not in the catalog)."""
    try:
        return PAPERS[paper_id]
    except KeyError:
        raise KeyError(f"unknown paper id: {paper_id!r}") from None


def pwg_media_name(paper_id: str, width_mm: float, height_mm: float) -> str:
    """PWG 5101.1 self-describing media name (IPP ``media``) of a paper.

    Catalog papers use their ``pwg_name``; anything else (custom size, roll sheet) gets
    ``custom_<id>_<w>x<h>mm`` with the short side first.
    """
    paper = PAPERS.get(paper_id)
    if paper is not None and paper.pwg_name:
        return paper.pwg_name
    lo, hi = sorted((width_mm, height_mm))
    slug = "".join(ch if ch.isalnum() else "-" for ch in paper_id.lower()).strip("-") or "custom"
    return f"custom_{slug}_{round(lo, 1):g}x{round(hi, 1):g}mm"


def list_papers() -> list[Paper]:
    return list(PAPERS.values())


def paper_size_in(paper: Paper) -> tuple[float, float]:
    """Exact inch dimensions of a paper, derived from its inch size if it has one."""
    if paper.width_in is not None and paper.height_in is not None:
        return (paper.width_in, paper.height_in)
    return (paper.width_mm / in_to_mm(1), paper.height_mm / in_to_mm(1))


def oriented(width_mm: float, height_mm: float, orientation: Orientation) -> tuple[float, float]:
    """Return ``(w, h)`` as portrait (``w <= h``) or landscape (``w >= h``).

    ``AUTO`` leaves the size unchanged (the planner decides).
    """
    lo, hi = sorted((width_mm, height_mm))
    if orientation is Orientation.PORTRAIT:
        return (lo, hi)
    if orientation is Orientation.LANDSCAPE:
        return (hi, lo)
    return (width_mm, height_mm)


def orientation_of(width_mm: float, height_mm: float) -> Orientation:
    """Portrait for ``w <= h``, landscape otherwise (never ``AUTO``)."""
    return Orientation.PORTRAIT if width_mm <= height_mm else Orientation.LANDSCAPE


class _Sized(Protocol):
    @property
    def width_mm(self) -> float: ...
    @property
    def height_mm(self) -> float: ...


@dataclass(frozen=True, slots=True)
class SizeMatch[T: _Sized]:
    """Result of matching a measured size against a list of standards."""

    standard: T
    landscape: bool  # the measured size is wider than tall
    deviation_mm: float  # largest per-axis deviation from the standard


def match_standard[S: _Sized](
    width_mm: float,
    height_mm: float,
    candidates: Iterable[S],
    *,
    abs_tol_mm: float = MATCH_ABS_TOL_MM,
    rel_tol: float = MATCH_REL_TOL,
) -> SizeMatch[S] | None:
    """Nearest standard within +-``abs_tol_mm`` or ``rel_tol`` of its side (whichever is larger).

    Orientation-agnostic. Returns ``None`` when nothing is close enough.
    """
    lo, hi = sorted((width_mm, height_mm))
    best: SizeMatch[S] | None = None
    for cand in candidates:
        c_lo, c_hi = sorted((cand.width_mm, cand.height_mm))
        d_lo, d_hi = abs(lo - c_lo), abs(hi - c_hi)
        if d_lo > max(abs_tol_mm, rel_tol * c_lo) or d_hi > max(abs_tol_mm, rel_tol * c_hi):
            continue
        dev = max(d_lo, d_hi)
        if best is None or dev < best.deviation_mm:
            best = SizeMatch(cand, width_mm > height_mm, dev)
    return best


def match_source_size(width_mm: float, height_mm: float) -> SizeMatch[SourceSize] | None:
    """Recognize a source page size (ANA-01)."""
    return match_standard(width_mm, height_mm, SOURCE_SIZES)


def match_paper(width_mm: float, height_mm: float) -> SizeMatch[Paper] | None:
    """Nearest output paper of the catalog (excluding ``CUSTOM``)."""
    return match_standard(width_mm, height_mm, PAPERS.values())


def is_standard_size(width_mm: float, height_mm: float, candidates: Sequence[_Sized]) -> bool:
    return match_standard(width_mm, height_mm, candidates) is not None
