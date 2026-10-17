"""Per-page legibility stats (ingest/stats.py): scaled CTM, work bounds."""

import pymupdf
import pytest

from a0td.domain.units import MM_PER_PT
from a0td.ingest import stats
from a0td.ingest.stats import page_stats


def _page_with(content: bytes) -> pymupdf.Page:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content)
    page.set_contents(xref)
    return page


def test_line_width_follows_the_cm_scale() -> None:
    # 0.25 user units drawn under a 4x matrix are 1 pt on the page. PyMuPDF already reports the
    # scaled width, so the stat must not multiply by the CTM scale a second time.
    page = _page_with(b"q 4 0 0 4 0 0 cm 0.25 w 10 10 m 90 10 l S Q")
    assert page_stats(page).line_min_mm == pytest.approx(1.0 * MM_PER_PT, rel=0.02)


def test_line_width_with_reducing_and_rotated_matrix() -> None:
    page = _page_with(b"q 0.5 0 0 0.5 0 0 cm 4 w 10 10 m 90 10 l S Q")  # 2 pt on the page
    assert page_stats(page).line_min_mm == pytest.approx(2.0 * MM_PER_PT, rel=0.02)
    rot = _page_with(b"q 0 3 -3 0 100 0 cm 1 w 10 10 m 90 10 l S Q")  # 3x, rotated 90 deg
    assert page_stats(rot).line_min_mm == pytest.approx(3.0 * MM_PER_PT, rel=0.02)


def test_heavy_content_is_skipped_and_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _page_with(b"1 w 10 10 m 90 10 l S")
    monkeypatch.setattr(stats, "_MAX_CONTENT_BYTES", 5)
    result = page_stats(page)
    assert result.incomplete
    assert result.line_min_mm is None
    assert result.font_p10_mm is None


def test_time_budget_stops_later_stages() -> None:
    page = _page_with(b"1 w 10 10 m 90 10 l S")
    result = page_stats(page, budget_s=-1.0)
    assert result.incomplete
    assert result.line_min_mm is None


def test_normal_page_is_complete() -> None:
    page = _page_with(b"1 w 10 10 m 90 10 l S")
    result = page_stats(page)
    assert not result.incomplete
    assert result.line_min_mm == pytest.approx(MM_PER_PT, rel=0.02)
