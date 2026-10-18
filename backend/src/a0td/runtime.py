"""Where the code runs: the Docker/CPython server or the in-browser Pyodide build (SPEC 17bis)."""

import os
import sys


def is_web() -> bool:
    """True in Pyodide (``emscripten``) or when ``A0TD_WEB`` is set (tests of the web build)."""
    if sys.platform == "emscripten":
        return True
    return os.environ.get("A0TD_WEB", "").strip().lower() in ("1", "true", "yes", "on")
