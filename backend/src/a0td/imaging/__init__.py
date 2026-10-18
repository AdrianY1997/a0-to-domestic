"""Image-processing facade used by ingest and render (SPEC 17bis, ``vips_compat``).

Every pixel operation of a0td goes through ``vips``, a namespace with the pyvips API subset the
code uses (``vips.Image.new_from_file``, ``vips.Error``, ...). The backend is chosen on first use:

* ``pyvips`` when it imports (Docker, streaming, the reference behaviour);
* otherwise :mod:`a0td.imaging.vips_compat` (Pillow + numpy: the in-browser Pyodide build).

``A0TD_IMAGING=pillow`` forces the Pillow backend (tests), ``A0TD_IMAGING=pyvips`` requires pyvips.
Nothing here imports pyvips until ``vips`` is first used.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Any

__all__ = ["backend_name", "icc_supported", "use_backend", "vips"]

_ENV = "A0TD_IMAGING"


def _import_backend(name: str) -> ModuleType:
    if name == "pyvips":
        import pyvips

        return pyvips
    from a0td.imaging import vips_compat

    return vips_compat


def _select() -> tuple[str, ModuleType]:
    choice = os.environ.get(_ENV, "").strip().lower()
    if choice not in ("", "auto", "pyvips", "pillow"):
        raise ValueError(f"{_ENV} must be 'pyvips', 'pillow' or 'auto', not {choice!r}")
    if choice != "pillow":
        try:
            return "pyvips", _import_backend("pyvips")
        except (ImportError, OSError):  # missing package or libvips shared library
            if choice == "pyvips":
                raise
    return "pillow", _import_backend("pillow")


class _Vips:
    """Lazy handle on the selected backend; attribute access forwards to it."""

    def __init__(self) -> None:
        self._name: str | None = None
        self._mod: ModuleType | None = None

    def _resolve(self) -> ModuleType:
        if self._mod is None:
            self._name, self._mod = _select()
        return self._mod

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._resolve(), attr)

    def use(self, name: str) -> None:
        if name not in ("pyvips", "pillow"):
            raise ValueError(name)
        self._mod, self._name = _import_backend(name), name


vips: Any = _Vips()


def backend_name() -> str:
    """``"pyvips"`` or ``"pillow"``."""
    vips._resolve()
    return str(vips._name)


def icc_supported() -> bool:
    """Whether embedded ICC profiles are applied (always with pyvips; littlecms for Pillow)."""
    if backend_name() == "pyvips":
        return True
    from a0td.imaging.vips_compat import icc_supported as _supported

    return _supported()


@contextmanager
def use_backend(name: str) -> Iterator[None]:
    """Temporarily switch the backend (tests comparing the two implementations)."""
    vips._resolve()
    previous = vips._name
    vips.use(name)
    try:
        yield
    finally:
        vips.use(str(previous))
