"""Printer calibration maths (SPEC 6.10 CAL-02, 5.5): ``k = measured / nominal`` per axis."""

from dataclasses import dataclass

from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.papers import PaperId
from a0td.domain.settings import Margins, PrinterProfile

K_MIN = 0.97
K_MAX = 1.03
MARGIN_SAFETY_MM = 0.5


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    kx: float
    ky: float
    margins: Margins


def compute_calibration(
    *,
    measured_x_mm: float,
    measured_y_mm: float,
    nominal_x_mm: float,
    nominal_y_mm: float,
    margins_mm: Margins,
) -> CalibrationResult:
    """Scale factors and stored margins (measured + 0.5 mm).

    Raises ``CALIBRATION_OUT_OF_RANGE`` when ``k`` leaves ``[0.97, 1.03]``: the page was most
    likely printed with "fit to page".
    """
    if min(measured_x_mm, measured_y_mm, nominal_x_mm, nominal_y_mm) <= 0:
        raise DomainError(ErrorCode.INVALID_SETTINGS, "lengths must be positive")
    kx = measured_x_mm / nominal_x_mm
    ky = measured_y_mm / nominal_y_mm
    if not (K_MIN <= kx <= K_MAX and K_MIN <= ky <= K_MAX):
        raise DomainError(
            ErrorCode.CALIBRATION_OUT_OF_RANGE,
            f"scale factor out of range (kx={kx:.4f}, ky={ky:.4f}); "
            "the page was probably printed with 'fit to page'",
            params={"kx": round(kx, 5), "ky": round(ky, 5), "min": K_MIN, "max": K_MAX},
        )

    def pad(v: float) -> float:
        return round(min(v + MARGIN_SAFETY_MM, 100.0), 3)

    stored = Margins(
        top=pad(margins_mm.top),
        right=pad(margins_mm.right),
        bottom=pad(margins_mm.bottom),
        left=pad(margins_mm.left),
    )
    return CalibrationResult(kx=round(kx, 5), ky=round(ky, 5), margins=stored)


def apply_calibration(
    printer: PrinterProfile, paper: PaperId, result: CalibrationResult
) -> PrinterProfile:
    """A copy of ``printer`` with the correction and the margins of ``paper`` stored."""
    paper_margins = dict(printer.paper_margins)
    paper_margins[paper] = result.margins
    return printer.model_copy(
        update={"scale_kx": result.kx, "scale_ky": result.ky, "paper_margins": paper_margins}
    )
