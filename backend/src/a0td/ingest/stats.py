"""Per-page analysis of a canonical PDF page: legibility stats (ANA-08), drawing scale (ANA-09)."""

import contextlib
import math
import re
import time
from collections import Counter
from dataclasses import dataclass

import numpy as np
import pymupdf

from a0td.domain.units import MM_PER_PT

# Scale denominators that are plausible for a drawing; "1:N" outside this set needs a keyword.
COMMON_DENOMINATORS = frozenset(
    (2, 5, 10, 20, 25, 30, 40, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 750, 1000)
)
_MAX_DENOMINATOR = 100_000
_MAX_DRAWINGS = 400_000  # beyond this the line stats are skipped (CAD plans with millions of paths)
_MAX_SPANS = 200_000
_LINE_PERCENTILE = 2.0
# Work bounds (the statistics are advisory: a page that is too heavy is skipped, not analysed).
# PyMuPDF cannot interrupt ``get_text`` / ``get_cdrawings`` once started, so the cost is bounded
# beforehand from the size of the content streams, and the time budget stops later stages.
_PAGE_BUDGET_S = 2.0
_MAX_CONTENT_BYTES = 24 * 1024 * 1024

_SEP = r"\s*(?::|/)\s*"
_KEYWORD = re.compile(
    rf"\b(?:escala|scale|esc|sc|e|ech|echelle|massstab)\b\.?\s*[:=.-]?\s*1{_SEP}(\d{{1,6}})(?!\d)",
    re.IGNORECASE,
)
_PLAIN = re.compile(rf"(?<![\d.,:/])1{_SEP}(\d{{1,6}})(?![\d.,:/])")


def detect_drawing_scale(text: str) -> int | None:
    """Denominator ``N`` of the drawing scale ``1:N`` written in ``text`` ("1:50", "E 1/100",
    "ESCALA 1:200"...), or ``None``. The most frequent candidate wins."""
    keyed: Counter[int] = Counter()
    for m in _KEYWORD.finditer(text):
        n = int(m.group(1))
        if 1 < n <= _MAX_DENOMINATOR:
            keyed[n] += 1
    if keyed:
        return keyed.most_common(1)[0][0]
    plain: Counter[int] = Counter()
    for m in _PLAIN.finditer(text):
        n = int(m.group(1))
        if n in COMMON_DENOMINATORS:
            plain[n] += 1
    return plain.most_common(1)[0][0] if plain else None


@dataclass(frozen=True, slots=True)
class PageStats:
    font_min_mm: float | None = None
    font_p10_mm: float | None = None
    line_min_mm: float | None = None
    detected_scale: int | None = None
    incomplete: bool = False  # a statistic was skipped (too heavy / out of time)


def _weighted_percentile(sizes: list[tuple[float, int]], q: float) -> float:
    """``q`` (0..1) percentile of font sizes weighted by character count."""
    ordered = sorted(sizes)
    total = sum(w for _, w in ordered)
    acc = 0.0
    for size, w in ordered:
        acc += w
        if acc >= q * total:
            return size
    return ordered[-1][0]


def _text_stats(
    page: pymupdf.Page, deadline: float
) -> tuple[float | None, float | None, int | None, bool]:
    data = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
    sizes: list[tuple[float, int]] = []
    chunks: list[str] = []
    n_spans = 0
    incomplete = False
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = str(span.get("text", ""))
                size = float(span.get("size", 0.0))
                n = len(text.strip())
                if n == 0 or not math.isfinite(size) or size <= 0:
                    continue
                sizes.append((size * MM_PER_PT, n))
                chunks.append(text)
                n_spans += 1
        if n_spans > _MAX_SPANS or time.monotonic() > deadline:
            incomplete = True
            break
    if not sizes:
        return None, None, None, incomplete
    smallest = min(s for s, _ in sizes)
    scale = detect_drawing_scale("\n".join(chunks))
    return smallest, _weighted_percentile(sizes, 0.10), scale, incomplete


def _content_bytes(page: pymupdf.Page) -> int:
    """Total size of the page's content streams, read from their ``/Length`` (nothing inflated)."""
    doc = page.parent
    total = 0
    for xref in page.get_contents():
        try:
            total += int(doc.xref_get_key(xref, "Length")[1])
        except (ValueError, TypeError):
            return _MAX_CONTENT_BYTES + 1  # unknown size: treat as too heavy
    return total


def _line_min(page: pymupdf.Page, deadline: float) -> tuple[float | None, bool]:
    """Thin-line statistic (mm): the 2nd percentile of the positive stroke widths.

    ``get_cdrawings`` already reports each stroke width in page units, i.e. multiplied by the
    scale of the CTM in force when it was painted (``sqrt(|det|)``): multiplying again would
    count the scale twice (``test_stats`` pins this with a scaled ``cm``).

    Approximation: layers are ignored, so strokes of hidden (OFF) layers count too.
    """
    drawings = page.get_cdrawings()
    if len(drawings) > _MAX_DRAWINGS or time.monotonic() > deadline:
        return None, True
    widths = [
        float(d["width"])
        for d in drawings
        if "s" in str(d.get("type", "")) and d.get("width") is not None and d["width"] > 0
    ]
    if not widths:
        return None, False
    value = float(np.percentile(np.asarray(widths), _LINE_PERCENTILE))
    return (value * MM_PER_PT if value > 0 else None), False


def page_stats(page: pymupdf.Page, budget_s: float = _PAGE_BUDGET_S) -> PageStats:
    """Stats of one PDF page; any failure of an individual statistic leaves it ``None``.

    Work is bounded before it is materialized: a page whose content streams exceed
    ``_MAX_CONTENT_BYTES`` is skipped entirely, and each stage checks the ``budget_s`` time budget.
    Skipped statistics set ``incomplete``.
    """
    with contextlib.suppress(Exception):
        if _content_bytes(page) > _MAX_CONTENT_BYTES:
            return PageStats(incomplete=True)
    deadline = time.monotonic() + budget_s
    font_min = font_p10 = None
    scale = None
    line_min = None
    incomplete = False
    with contextlib.suppress(Exception):
        font_min, font_p10, scale, incomplete = _text_stats(page, deadline)
    if time.monotonic() > deadline:
        return PageStats(font_min, font_p10, None, scale, incomplete=True)
    with contextlib.suppress(Exception):
        line_min, skipped = _line_min(page, deadline)
        incomplete = incomplete or skipped
    return PageStats(font_min, font_p10, line_min, scale, incomplete)
