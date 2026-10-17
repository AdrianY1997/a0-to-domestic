"""Output helpers: name sanitizing, filename template (SAL-03), strings, geometry maps."""

import pytest

from a0td.domain.errors import DomainError, ErrorCode
from a0td.domain.geometry import Rect
from a0td.domain.page import ViewTransform
from a0td.domain.papers import Orientation
from a0td.domain.plan import Counts, Grid, PaperRef, Plan, Range2
from a0td.domain.settings import Margins
from a0td.render.geometry import ViewMap, visible_rect
from a0td.render.outputs import build_filename, sanitize_name, scale_text, unique_name
from a0td.render.strings import get_strings


def _plan(scale: float = 1.0) -> Plan:
    return Plan(
        page_index=0,
        view_size_mm=(841, 1189),
        source_rect_mm=(0, 0, 841, 1189),
        orientation=Orientation.LANDSCAPE,
        paper=PaperRef(id="A4", width_mm=297, height_mm=210),
        margins_mm=Margins.uniform(5),
        content_mm=(287, 200),
        content_origin_mm=(5, 5),
        overlap_mm=10,
        step_mm=(277, 190),
        scale=scale,
        scale_label="100 %",
        grid=Grid(cols=3, rows=7),
        coverage_mm=(841, 1340),
        origin_mm=(0, -75.5),
        offset_range_mm=Range2(x=(0, 0), y=(-151, 0)),
        assembled_size_mm=(841, 1189),
        effective_dpi=None,
        sheets=[],
        counts=Counts(total=21, printed=21, blank=0, deselected=0),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Plano A0.pdf", "Plano_A0_pdf"),
        ("Ñandú ción", "Nandu_cion"),
        ("../../etc/passwd", "etc_passwd"),
        ("   ", "document"),
        ("日本語", "document"),
        ("a" * 100, "a" * 60),
    ],
)
def test_sanitize_name(raw: str, expected: str) -> None:
    assert sanitize_name(raw) == expected
    out = sanitize_name(raw)
    assert len(out) <= 60
    assert all(c.isascii() and (c.isalnum() or c in "_-") for c in out)


def test_scale_text() -> None:
    assert scale_text(1.0) == "100"
    assert scale_text(0.2378) == "23.78"
    assert scale_text(0.5) == "50"


def test_default_template() -> None:
    name = build_filename(
        "{name}_{paper}-{orientation}_{cols}x{rows}_{scale}pct", name="Mi plano", plan=_plan()
    )
    assert name == "Mi_plano_A4-landscape_3x7_100pct.pdf"


def test_template_variants_and_suffix() -> None:
    assert build_filename("{name}", name="x", plan=_plan(), suffix="_test") == "x_test.pdf"
    assert build_filename("tiles/{name}", name="x", plan=_plan()) == "tiles_x.pdf"
    assert build_filename("{name}_{scale}", name="x", plan=_plan(0.2378)) == "x_23.78.pdf"


@pytest.mark.parametrize("template", ["{nope}", "{name", "{0}", "{name!z}"])
def test_bad_template_is_invalid_settings(template: str) -> None:
    with pytest.raises(DomainError) as exc:
        build_filename(template, name="x", plan=_plan())
    assert exc.value.code is ErrorCode.INVALID_SETTINGS


def test_unique_name() -> None:
    assert unique_name("a.pdf", set()) == "a.pdf"
    assert unique_name("a.pdf", {"a.pdf"}) == "a-2.pdf"
    assert unique_name("a.pdf", {"a.pdf", "a-2.pdf"}) == "a-3.pdf"


def test_strings_locales_have_same_keys() -> None:
    es, en = get_strings("es"), get_strings("en-US")
    assert es.keys() == en.keys()
    assert get_strings("xx") is get_strings("es")
    assert all(v for v in es.values())
    assert all(v for v in en.values())


def test_view_map_and_visible_rect() -> None:
    vm = ViewMap(ox=5, oy=5, vx0=100, vy0=50, scale=0.5)
    assert vm.point(100, 50) == (5, 5)
    assert vm.rect(Rect(100, 50, 300, 250)) == Rect(5, 5, 105, 105)
    page = Rect(0, 0, 200, 200)
    assert visible_rect(Rect(150, 150, 350, 350), Rect(0, 0, 400, 400), page) == Rect(
        150, 150, 200, 200
    )
    assert visible_rect(Rect(250, 0, 300, 50), Rect(0, 0, 400, 400), page) is None
    assert visible_rect(Rect(199.99999, 0, 300, 50), Rect(0, 0, 400, 400), page) is None


def test_view_transform_roundtrip_matches_clip_use() -> None:
    tf = ViewTransform(Rect(0, 0, 100, 200), 90)
    r = Rect(10, 20, 60, 90)
    assert tf.to_view(tf.to_canonical(r)) == r
