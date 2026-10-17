"""Lazy access to the render engine (``a0td.render.engine``, implemented by WP-07).

The module is imported on demand so the API and the ingest worker work without it. The
``A0TD_RENDER_ENGINE`` environment variable overrides the module path (used by tests to plug a
fake engine in, also inside worker subprocesses).
"""

import importlib
import os
from types import ModuleType

DEFAULT_ENGINE_MODULE = "a0td.render.engine"


class RenderUnavailableError(RuntimeError):
    """The render engine (or one of its functions) is not installed."""


def load_engine() -> ModuleType:
    name = os.environ.get("A0TD_RENDER_ENGINE") or DEFAULT_ENGINE_MODULE
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise RenderUnavailableError(f"render engine '{name}' is not available: {exc}") from exc


def engine_function(attr: str) -> object:
    engine = load_engine()
    fn = getattr(engine, attr, None)
    if fn is None:
        raise RenderUnavailableError(f"render engine has no '{attr}'")
    return fn
