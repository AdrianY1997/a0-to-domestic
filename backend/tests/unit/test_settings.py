import json
from typing import Any

import pytest
from pydantic import ValidationError

from a0td.domain.errors import DomainError, ErrorCode, WarningCode
from a0td.domain.papers import Orientation, PaperId
from a0td.domain.settings import (
    BUILTIN_PRESETS,
    BUILTIN_PRINTERS,
    FinalSizeScale,
    FitGridScale,
    JobSettings,
    JoinMethod,
    Margins,
    PercentScale,
    deep_merge,
    get_builtin_preset,
    get_builtin_printer,
    merge_settings,
    parse_page_ranges,
)


def test_defaults_match_appendix_b() -> None:
    s = JobSettings()
    assert s.schema_version == 1
    assert s.source.pages == "all"
    assert s.target.paper is PaperId.A4
    assert s.target.printer_id == "builtin-generic"
    assert s.target.orientation is Orientation.AUTO
    assert s.scale == PercentScale(percent=100)
    assert s.tiling.overlap_mm == 10
    assert s.tiling.align.x.value == "center"
    assert s.marks.color == "#808080"
    assert s.marks.line_width_pt == 0.5
    assert s.marks.assembly_page.value == "first"
    assert s.color.threshold == 160
    assert [f.value for f in s.output.formats] == ["pdf"]
    assert s.output.filename_template == "{name}_{paper}-{orientation}_{cols}x{rows}_{scale}pct"


def test_roundtrip_json() -> None:
    s = JobSettings()
    again = JobSettings.model_validate(json.loads(s.model_dump_json()))
    assert again == s


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        JobSettings.model_validate({"bogus": 1})


@pytest.mark.parametrize(
    "scale",
    [
        {"mode": "percent", "percent": 50},
        {"mode": "fit_sheet"},
        {"mode": "fit_grid", "cols": 2, "rows": 3},
        {"mode": "fit_count", "max_sheets": 6},
        {"mode": "final_size", "width_mm": 500},
        {"mode": "final_size", "height_mm": 500},
        {"mode": "drawing_scale", "from_denominator": 50, "to_denominator": 100},
    ],
)
def test_scale_union_valid(scale: dict[str, Any]) -> None:
    assert JobSettings.model_validate({"scale": scale}).scale.mode == scale["mode"]


@pytest.mark.parametrize(
    "scale",
    [
        {"mode": "percent", "percent": 0.5},
        {"mode": "percent", "percent": 1001},
        {"mode": "fit_grid", "cols": 0, "rows": 1},
        {"mode": "final_size"},
        {"mode": "final_size", "width_mm": 1, "height_mm": 1},
        {"mode": "drawing_scale", "from_denominator": 0, "to_denominator": 1},
        {"mode": "nope"},
    ],
)
def test_scale_union_invalid(scale: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        JobSettings.model_validate({"scale": scale})


def test_drawing_scale_factor() -> None:
    s = JobSettings.model_validate(
        {"scale": {"mode": "drawing_scale", "from_denominator": 50, "to_denominator": 100}}
    )
    assert s.scale.mode == "drawing_scale"
    assert s.scale.factor == pytest.approx(0.5)  # type: ignore[union-attr]


def test_butt_forces_zero_overlap() -> None:
    s = JobSettings.model_validate({"tiling": {"join": "butt", "overlap_mm": 10}})
    assert s.tiling.join is JoinMethod.BUTT
    assert s.tiling.overlap_mm == 0


@pytest.mark.parametrize("overlap", [2.9, 50.1, 0])
def test_overlap_range(overlap: float) -> None:
    with pytest.raises(ValidationError):
        JobSettings.model_validate({"tiling": {"overlap_mm": overlap}})


@pytest.mark.parametrize("overlap", [3, 50, 12.5])
def test_overlap_range_ok(overlap: float) -> None:
    assert JobSettings.model_validate({"tiling": {"overlap_mm": overlap}}).tiling.overlap_mm


def test_custom_paper_rules() -> None:
    with pytest.raises(ValidationError, match="custom_paper_mm"):
        JobSettings.model_validate({"target": {"paper": "CUSTOM"}})
    ok = JobSettings.model_validate(
        {"target": {"paper": "CUSTOM", "custom_paper_mm": {"width": 300, "height": 1000}}}
    )
    assert ok.target.custom_paper_mm is not None
    for bad in ({"width": 40, "height": 100}, {"width": 600, "height": 600}):
        with pytest.raises(ValidationError):
            JobSettings.model_validate({"target": {"paper": "CUSTOM", "custom_paper_mm": bad}})


def test_info_band_rules() -> None:
    with pytest.raises(ValidationError):
        JobSettings.model_validate({"target": {"info_band": {"position": "top"}}})
    s = JobSettings.model_validate({"target": {"info_band": {"position": "none", "height_mm": 9}}})
    assert s.target.info_band.height_mm == 0


def test_misc_validation() -> None:
    for patch in (
        {"source": {"rotate": 45}},
        {"marks": {"color": "red"}},
        {"output": {"dpi": 123}},
        {"output": {"formats": []}},
        {"color": {"threshold": 300}},
        {"source": {"pages": "0"}},
    ):
        with pytest.raises(ValidationError):
            JobSettings.model_validate(patch)


def test_page_ranges() -> None:
    assert parse_page_ranges("all", 4) == [1, 2, 3, 4]
    assert parse_page_ranges("1,3-5", 9) == [1, 3, 4, 5]
    assert parse_page_ranges("5, 1-2, 2", 9) == [1, 2, 5]
    for bad in ("", "a", "3-1", "0", "1,,2", "1-"):
        with pytest.raises(ValueError, match="invalid page range"):
            parse_page_ranges(bad, 9)
    with pytest.raises(ValueError, match="out of range"):
        parse_page_ranges("1-10", 9)
    assert JobSettings.model_validate({"source": {"pages": " 1, 3-5 "}}).source.pages == "1,3-5"


def test_margins_landscape_safe() -> None:
    m = Margins(top=3, right=3, bottom=5, left=4)
    safe = m.landscape_safe()
    assert (safe.top, safe.bottom, safe.left, safe.right) == (4, 4, 5, 5)
    assert m.for_orientation(Orientation.PORTRAIT) == m
    assert m.for_orientation(Orientation.LANDSCAPE) == safe


def test_deep_merge_does_not_mutate_and_replaces_modes() -> None:
    base = {"a": {"x": 1, "y": 2}, "scale": {"mode": "percent", "percent": 100}}
    out = deep_merge(base, {"a": {"y": 3}, "scale": {"mode": "fit_sheet"}})
    assert out == {"a": {"x": 1, "y": 3}, "scale": {"mode": "fit_sheet"}}
    assert base["a"] == {"x": 1, "y": 2}


def test_builtin_printers() -> None:
    by_id = {p.id: p for p in BUILTIN_PRINTERS}
    assert set(by_id) == {
        "builtin-generic",
        "builtin-inkjet",
        "builtin-laser",
        "builtin-borderless",
    }
    assert by_id["builtin-generic"].default_margins == Margins.uniform(5)
    ink = by_id["builtin-inkjet"].default_margins
    assert (ink.top, ink.right, ink.bottom, ink.left) == (3, 3, 5, 3)
    assert by_id["builtin-laser"].default_margins == Margins.uniform(4.5)
    assert by_id["builtin-borderless"].default_margins == Margins.uniform(0)
    assert by_id["builtin-generic"].margins_for(PaperId.A3) == Margins.uniform(5)
    assert get_builtin_printer("builtin-laser") is by_id["builtin-laser"]
    assert get_builtin_printer("zzz") is None
    assert PaperId.CUSTOM not in by_id["builtin-generic"].supported_papers


def test_all_builtin_presets_apply() -> None:
    assert len(BUILTIN_PRESETS) == 7
    for preset in BUILTIN_PRESETS:
        assert preset.builtin
        assert isinstance(preset.apply(), JobSettings)


def test_preset_contents() -> None:
    letter = get_builtin_preset("builtin-real-letter")
    assert letter is not None
    assert letter.apply().target.paper is PaperId.LETTER

    fit = get_builtin_preset("builtin-fit-a4")
    assert fit is not None
    s = fit.apply()
    assert s.scale.mode == "fit_sheet"
    assert s.marks.assembly_page.value == "none"
    assert not any([s.marks.cut_lines, s.marks.labels, s.marks.ruler, s.marks.alignment_marks])

    poster = get_builtin_preset("builtin-poster-2x2-a3")
    assert poster is not None
    s = poster.apply()
    assert s.target.paper is PaperId.A3
    assert s.scale == FitGridScale(cols=2, rows=2)

    sewing = get_builtin_preset("builtin-sewing-a4")
    assert sewing is not None
    s = sewing.apply()
    assert s.tiling.join is JoinMethod.BUTT
    assert s.tiling.overlap_mm == 0
    assert s.marks.join_diamonds
    assert s.marks.test_square.value == "first_sheet"
    assert s.tiling.skip_blank


def test_merge_over_custom_base() -> None:
    base = JobSettings.model_validate({"scale": {"mode": "final_size", "width_mm": 400}})
    assert isinstance(base.scale, FinalSizeScale)
    merged = merge_settings({"scale": {"mode": "percent", "percent": 50}}, base)
    assert merged.scale == PercentScale(percent=50)


def test_error_codes() -> None:
    assert len(ErrorCode) == 27
    assert len(WarningCode) == 17
    err = DomainError(ErrorCode.INVALID_SETTINGS, "bad", {"field": "x"})
    assert err.code is ErrorCode.INVALID_SETTINGS
    assert err.params == {"field": "x"}
    assert str(err) == "bad"
    assert DomainError(ErrorCode.NOT_FOUND).detail == "NOT_FOUND"
