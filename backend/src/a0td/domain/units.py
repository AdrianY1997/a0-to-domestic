"""Unit conversions. The domain works in millimetres; points only appear at render time."""

import math

MM_PER_INCH = 25.4
PT_PER_INCH = 72.0
MM_PER_PT = MM_PER_INCH / PT_PER_INCH
EPS = 1e-9


def mm_to_pt(mm: float) -> float:
    return mm / MM_PER_PT


def pt_to_mm(pt: float) -> float:
    return pt * MM_PER_PT


def mm_to_in(mm: float) -> float:
    return mm / MM_PER_INCH


def in_to_mm(inches: float) -> float:
    return inches * MM_PER_INCH


def ceil_eps(x: float) -> int:
    """Tolerant ceiling: ``ceil(x - 1e-9)``. Mandatory for every tile-count computation."""
    return math.ceil(x - EPS)


def floor_eps(x: float) -> int:
    """Tolerant floor, the counterpart of :func:`ceil_eps`."""
    return math.floor(x + EPS)


def dpi_to_px_per_mm(dpi: float) -> float:
    return dpi / MM_PER_INCH


def px_per_mm_to_dpi(px_per_mm: float) -> float:
    return px_per_mm * MM_PER_INCH
