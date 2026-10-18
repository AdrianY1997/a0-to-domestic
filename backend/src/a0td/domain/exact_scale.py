"""When must the planner/renderer treat the scale as exact (no "save sheets" suggestions)?

``exact_scale`` is ``True`` for patterns and plans printed 1:1, where shrinking the drawing to
save a sheet would be wrong (SPEC 5.11, WP-02 ``exact_scale``):

* the settings came from a pattern/plan-type preset (``EXACT_SCALE_PRESET_IDS``), or
* the scale is ``percent`` 100 with the ``butt`` join (the sewing-pattern combination).
"""

from a0td.domain.settings import JobSettings, JoinMethod, PercentScale

# Built-in presets whose output must keep the drawn scale (pattern / plan presets).
EXACT_SCALE_PRESET_IDS: frozenset[str] = frozenset({"builtin-sewing-a4"})


def is_exact_scale(settings: JobSettings, preset_id: str | None = None) -> bool:
    if preset_id is not None and preset_id in EXACT_SCALE_PRESET_IDS:
        return True
    scale = settings.scale
    return (
        isinstance(scale, PercentScale)
        and abs(scale.percent - 100.0) < 1e-9
        and settings.tiling.join is JoinMethod.BUTT
    )
