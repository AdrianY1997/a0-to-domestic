"""ANA-08: legibility warnings from the page's text / line statistics and the plan's scale."""

from a0td.domain.errors import WarningCode
from a0td.domain.page import PageGeometry
from a0td.domain.plan import PlanWarning
from a0td.domain.units import MM_PER_PT

MIN_TEXT_PT = 5.0  # body text below this (after scaling) is hard to read
MIN_LINE_MM = 0.1  # strokes thinner than this (after scaling) may not print


def legibility_warnings(page: PageGeometry, scale: float) -> list[PlanWarning]:
    """``SMALL_TEXT`` / ``THIN_LINES`` for ``page`` printed at ``scale``."""
    out: list[PlanWarning] = []
    if page.font_p10_mm is not None:
        p10_pt = page.font_p10_mm * scale / MM_PER_PT
        if p10_pt < MIN_TEXT_PT:
            min_pt = (page.font_min_mm or page.font_p10_mm) * scale / MM_PER_PT
            out.append(
                PlanWarning(
                    code=WarningCode.SMALL_TEXT,
                    params={"p10_pt": round(p10_pt, 2), "min_pt": round(min(min_pt, p10_pt), 2)},
                )
            )
    if page.line_min_mm is not None:
        width = page.line_min_mm * scale
        if width < MIN_LINE_MM:
            out.append(PlanWarning(code=WarningCode.THIN_LINES, params={"min_mm": round(width, 3)}))
    return out
