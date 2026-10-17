"""PDF optional content (SPEC 6.2 ANA-07, 6.6 MAR-05): hidden source layers and the marks layer.

``show_pdf_page`` grafts the source's OCG objects into the output document but not its
``/OCProperties``, so without help every layer would be visible. :func:`finish_layers` writes the
catalog entry: hidden layers are ``/OFF`` and carry ``/Usage`` with ``ViewState`` and
``PrintState`` ``OFF`` plus an ``/AS`` auto-state array for the *View*, *Print* and *Export*
events, so conforming viewers neither show nor print them. Rendering the output (MuPDF) honours
the same configuration, which keeps the editor preview consistent with the file.
"""

import re
from pathlib import Path

import pymupdf

from a0td.ingest.models import Layer

_MARKS_NAME = {"es": "Marcas", "en": "Marks"}
_OCG_TYPE = "/OCG"
_OFF_USAGE = "<</Print<</PrintState/OFF>>/View<</ViewState/OFF>>/Export<</ExportState/OFF>>>>"


def marks_layer_name(locale: str) -> str:
    return _MARKS_NAME.get(locale, _MARKS_NAME["es"])


def _names_for(layers: list[Layer], entries: list[str]) -> set[str]:
    """Names of the layers selected by ``entries`` (each: a canonical xref or a layer name)."""
    wanted = {e.strip() for e in entries if e.strip()}
    if not wanted:
        return set()
    return {la.name for la in layers if str(la.xref) in wanted or la.name in wanted}


def effective_off_names(
    layers: list[Layer], hidden: list[str], visible: list[str] | None = None
) -> set[str]:
    """Layer names that must be OFF: ``(source-off - visible) | hidden`` (ANA-07).

    A layer that is OFF in the source stays OFF unless listed in ``visible``; ``hidden`` turns
    a layer off explicitly (and wins over ``visible``). Layers are matched by name: xrefs are
    only meaningful in the canonical PDF, never in a derived copy.
    """
    source_off = {la.name for la in layers if not la.on}
    return (source_off - _names_for(layers, visible or [])) | _names_for(layers, hidden)


def ocg_names(doc: pymupdf.Document) -> dict[int, str]:
    """``{xref: name}`` of the OCGs of ``doc``."""
    try:
        ocgs = doc.get_ocgs() or {}
    except Exception:
        return {}
    return {int(x): str(info.get("name", "")) for x, info in ocgs.items()}


def layer_states(
    doc: pymupdf.Document,
    layers: list[Layer],
    hidden: list[str],
    visible: list[str] | None = None,
) -> list[int]:
    """Xrefs, in ``doc``, of the OCGs that must be OFF, resolved by OCG name (never by the
    canonical's xrefs: ``doc`` may be a derived copy with other numbering)."""
    off_names = effective_off_names(layers, hidden, visible)
    return [x for x, n in ocg_names(doc).items() if n in off_names]


def has_layer_changes(layers: list[Layer], hidden: list[str], visible: list[str] | None) -> bool:
    """Would any layer be switched off, or a source-off layer be forced on?"""
    source_off = {la.name for la in layers if not la.on}
    return bool(effective_off_names(layers, hidden, visible) or source_off)


_TOKEN = re.compile(r"\[|\]|/\w+|\d+ \d+ R")


def _parse_ve(tokens: list[str], pos: int) -> tuple[object, int]:
    tok = tokens[pos]
    if tok == "[":
        items: list[object] = []
        pos += 1
        while tokens[pos] != "]":
            item, pos = _parse_ve(tokens, pos)
            items.append(item)
        return items, pos + 1
    if tok.endswith(" R"):
        return int(tok.split()[0]), pos + 1
    return tok, pos + 1


def _eval_ve(expr: object, off: set[int]) -> bool:
    """Visibility given by a visibility expression ``[/And|/Or|/Not operands...]``."""
    if isinstance(expr, int):
        return expr not in off
    if not isinstance(expr, list) or not expr:
        raise ValueError("bad visibility expression")
    op, args = expr[0], expr[1:]
    if op == "/Not" and len(args) == 1:
        return not _eval_ve(args[0], off)
    if op == "/And":
        return all(_eval_ve(a, off) for a in args)
    if op == "/Or":
        return any(_eval_ve(a, off) for a in args)
    raise ValueError("bad visibility expression")


def _ocmd_visible(doc: pymupdf.Document, xref: int, off: set[int]) -> bool:
    """Visibility of an OCMD per PDF 32000 8.11.2.2, with the OCGs in ``off`` switched off."""
    ve = doc.xref_get_key(xref, "VE")
    if ve[0] != "null":
        tokens = _TOKEN.findall(ve[1])
        tree, _ = _parse_ve(tokens, 0)
        return _eval_ve(tree, off)
    refs = [int(m) for m in re.findall(r"(\d+) 0 R", doc.xref_get_key(xref, "OCGs")[1])]
    if not refs:
        return True
    states = [r not in off for r in refs]  # True: the group is ON
    policy = doc.xref_get_key(xref, "P")[1]
    if policy == "/AllOn":
        return all(states)
    if policy == "/AnyOff":
        return not all(states)
    if policy == "/AllOff":
        return not any(states)
    return any(states)  # /AnyOn, the default


def normalize_ocmds(doc: pymupdf.Document, off: list[int]) -> None:
    """Rewrite every OCMD of ``doc`` into a form every viewer evaluates the same way.

    OCMD policies other than *AnyOn* (``/P`` AllOn / AnyOff / AllOff, ``/VE``) are applied
    unevenly: MuPDF, for one, gets a single-group AllOn / AnyOff wrong. The visibility is
    evaluated here, with ``off`` as the OFF groups, and the OCMD becomes either an empty one
    (always visible) or *AnyOn* over a group that is OFF (hidden). Without OFF groups the
    document is left alone, because the default state is then evaluated correctly everywhere.
    """
    if not off:
        return
    off_set = set(off)
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Type")[1] != "/OCMD":
                continue
            visible = _ocmd_visible(doc, xref, off_set)
        except Exception:  # unreadable / malformed: leave it to the viewer
            continue
        if visible:
            doc.update_object(xref, "<</Type/OCMD>>")
        else:
            doc.update_object(xref, f"<</Type/OCMD/OCGs[{off[0]} 0 R]/P/AnyOn>>")


def new_marks_ocg(doc: pymupdf.Document, locale: str) -> int:
    """Create the ``Marcas`` / ``Marks`` OCG (visible and printable); returns its xref."""
    xref: int = doc.get_new_xref()
    name = marks_layer_name(locale)
    doc.update_object(
        xref,
        f"<</Type/OCG/Name({name})/Intent[/View]/Usage<</CreatorInfo<</Creator(a0-to-domestic)"
        "/Subtype/Artwork>>/Print<</PrintState/ON>>/View<</ViewState/ON>>>>>>",
    )
    return xref


def _all_ocgs(doc: pymupdf.Document) -> dict[int, str]:
    found: dict[int, str] = {}
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Type")[1] != _OCG_TYPE:
                continue
            raw = doc.xref_get_key(xref, "Name")[1]
        except Exception:  # an unreadable object is not an OCG we can configure
            continue
        found[xref] = raw
    return found


def finish_layers(doc: pymupdf.Document, off_names: set[str], *, marks_oc: int = 0) -> int:
    """Write ``/OCProperties`` for every OCG in ``doc``; layers named in ``off_names`` go OFF.

    The marks OCG (``marks_oc``) is never switched off and never matched by name, so a source
    layer called like it is handled on its own. Returns how many layers were switched off. A
    document without OCGs is left untouched.
    """
    ocgs = _all_ocgs(doc)
    if not ocgs:
        return 0
    off = [x for x, name in ocgs.items() if name in off_names and x != marks_oc]
    normalize_ocmds(doc, off)
    on_refs = " ".join(f"{x} 0 R" for x in ocgs if x not in off)
    for x in off:
        doc.xref_set_key(x, "Usage", _OFF_USAGE)
    refs = " ".join(f"{x} 0 R" for x in ocgs)
    off_refs = " ".join(f"{x} 0 R" for x in off)
    auto = ""
    if off:
        events = "".join(
            f"<</Event/{ev}/Category[/{cat}]/OCGs[{off_refs}]>>"
            for ev, cat in (("View", "View"), ("Print", "Print"), ("Export", "Export"))
        )
        auto = f"/AS[{events}]"
    doc.xref_set_key(
        doc.pdf_catalog(),
        "OCProperties",
        f"<</OCGs[{refs}]/D<</BaseState/ON/ON[{on_refs}]/OFF[{off_refs}]/Order[{refs}]{auto}>>>>",
    )
    return len(off)


def reopen_with_layers(doc: pymupdf.Document, off: list[int]) -> pymupdf.Document:
    """``doc`` with exactly the OCGs ``off`` switched off, as a freshly opened document.

    MuPDF reads the optional-content configuration when a document is loaded, so a change made
    in memory only shows in renders after the document is serialized and opened again. The
    original is closed when a new document is returned. The configuration is rewritten whole
    (every other group ON), so a group that is OFF in the source can be switched back on.
    """
    ocgs = list(ocg_names(doc))
    if not ocgs:
        return doc
    normalize_ocmds(doc, off)
    refs = " ".join(f"{x} 0 R" for x in ocgs)
    on_refs = " ".join(f"{x} 0 R" for x in ocgs if x not in off)
    off_refs = " ".join(f"{x} 0 R" for x in off)
    doc.xref_set_key(
        doc.pdf_catalog(),
        "OCProperties",
        f"<</OCGs[{refs}]/D<</BaseState/ON/ON[{on_refs}]/OFF[{off_refs}]/Order[{refs}]>>>>",
    )
    data = doc.tobytes(garbage=0, deflate=False)
    doc.close()
    return pymupdf.open("pdf", data)


def layered_preview_webp(
    canonical: Path,
    index: int,
    layers: list[Layer],
    hidden: list[str],
    visible: list[str] | None,
    max_px: int,
) -> bytes:
    """WEBP of canonical page ``index`` with the effective layer state (editor preview, ANA-07)."""
    import io

    from PIL import Image

    doc = pymupdf.open(canonical)
    try:
        doc = reopen_with_layers(doc, layer_states(doc, layers, hidden, visible))
        page = doc[index]
        zoom = max(1, max_px) / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False
        )
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=85)
    return buf.getvalue()
