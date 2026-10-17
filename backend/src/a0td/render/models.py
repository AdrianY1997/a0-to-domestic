"""Result models of the render stage (re-exported by :mod:`a0td.render.engine`)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from a0td.domain.plan import Plan


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutputFile(_Model):
    name: str
    kind: Literal["pdf", "zip"]
    size_bytes: int
    pages: int


class RenderWarning(_Model):
    """A non-fatal render-time notice (e.g. an option that is not implemented yet)."""

    code: str
    severity: Literal["info", "warning"] = "warning"
    params: dict[str, Any] = Field(default_factory=dict)


class InkSheet(_Model):
    id: str
    coverage: float  # mean darkness of the sheet, 0 (blank) .. 1 (solid black)
    equiv_cm2: float  # coverage x sheet area: cm2 of solid ink


class InkEstimate(_Model):
    """COL-07: ink coverage estimated from low-resolution renders of the printed sheets."""

    sheets: list[InkSheet]
    total_coverage: float  # area-weighted mean over all printed sheets
    total_equiv_cm2: float


class RenderResult(_Model):
    outputs: list[OutputFile]
    plans: list[Plan]
    warnings: list[RenderWarning] = Field(default_factory=list)
    ink: InkEstimate | None = None
