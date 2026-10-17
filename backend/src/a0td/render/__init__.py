"""Render stage (WP-07): tiled PDF output from an ingested document (SPEC 5.9, 5.10, 9.7)."""

from a0td.render.engine import (
    OutputFile,
    RenderResult,
    RenderWarning,
    render_job,
    render_sheet_preview,
)

__all__ = ["OutputFile", "RenderResult", "RenderWarning", "render_job", "render_sheet_preview"]
