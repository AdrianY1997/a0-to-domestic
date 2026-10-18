"""SVG / SVGZ converter: sanitize, then ``rsvg-convert`` -> PDF; Inkscape only on failure.

The SVG is parsed with ``defusedxml`` (DTDs, entities and external references are rejected, which
neutralizes XXE) and rewritten without remote / file references, scripts or ``foreignObject``
before any renderer sees it (SPEC 14).

An SVG whose root has no physical width and height (mm, cm, in, pt, pc) has no real size:
it is scaled to fit an A0 sheet in its own orientation and flagged ``PHYSICAL_SIZE_ASSUMED``,
exactly like a raster without DPI (ANA-03). ``set_physical_size`` fixes it afterwards.
"""

import gzip
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import pymupdf
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as DefusedET

from a0td.domain.errors import DomainError, ErrorCode
from a0td.ingest.analyze import ASSUMED_PAPER_ID, _standard_match, fit_to_a0_mm_per_px
from a0td.ingest.converters.base import ConvertContext, run_tool, tool_available
from a0td.ingest.converters.common import fresh_dir, pdf_to_canonical, tool_env
from a0td.ingest.models import CanonicalDocument, DocFormat
from a0td.ingest.vector_size import NATIVE_SIZE_FILE, rescale_pdf_pages
from a0td.runtime import is_web

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_MAX_SVG_BYTES = 200 * 1024 * 1024
_UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6}
_LENGTH = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Z%]*)\s*$")
_URL_REF = re.compile(r"url\(\s*(?!['\"]?\s*#)[^)]*\)", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import[^;]*;?", re.IGNORECASE)
_DATA_IMAGE = re.compile(r"data:image/(?:png|jpe?g|gif|webp)[;,]", re.IGNORECASE)
_PLAIN_DOCTYPE = re.compile(rb"<!DOCTYPE[^>\[]*>", re.IGNORECASE)  # no internal subset
_DROP_TAGS = frozenset({"script", "foreignObject"})
_CSS_COMMENT = re.compile(r"/\*.*?(?:\*/|\Z)", re.DOTALL)
_MAX_DEPTH = 256
_MAX_DATA_URI_BYTES = 50 * 1024 * 1024  # total payload of embedded data: images
_MIN_PX = 1e-3
_ANIMATION_TAGS = frozenset({"set", "animate"})


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_svg_bytes(src: Path, gz: bool) -> bytes:
    """File content, gunzipped (bounded) for SVGZ."""
    try:
        if gz:
            with gzip.open(src, "rb") as fh:
                data = fh.read(_MAX_SVG_BYTES + 1)
        else:
            data = src.read_bytes()
    except (OSError, EOFError) as exc:
        raise DomainError(ErrorCode.CORRUPT_FILE, "the SVG cannot be read") from exc
    if len(data) > _MAX_SVG_BYTES:
        raise DomainError(ErrorCode.CORRUPT_FILE, "the SVG is too large once decompressed")
    return data


def _safe_href(value: str) -> bool:
    v = value.strip()
    return v.startswith("#") or _DATA_IMAGE.match(v) is not None


def _strip_at_rules(css: str) -> str:
    """Remove every CSS @-rule wholesale: ``@x ...;`` or ``@x ... { ...balanced... }``."""
    out: list[str] = []
    i, n = 0, len(css)
    while i < n:
        ch = css[i]
        if ch != "@":
            out.append(ch)
            i += 1
            continue
        depth = 0
        i += 1
        while i < n:
            c = css[i]
            i += 1
            if c == ";" and depth == 0:
                break
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth <= 0:
                    break
    return "".join(out)


def _clean_css(css: str) -> str:
    css = _CSS_COMMENT.sub(" ", css)
    return _URL_REF.sub("none", _strip_at_rules(css))


def sanitize_svg(data: bytes) -> tuple[bytes, ET.Element]:
    """Parse safely and strip active / external content. Returns ``(clean_bytes, root)``."""
    data = _PLAIN_DOCTYPE.sub(b"", data, count=1)  # e.g. the Illustrator PUBLIC doctype
    try:
        root = DefusedET.fromstring(data, forbid_dtd=True)
    except DefusedXmlException as exc:
        raise DomainError(
            ErrorCode.CORRUPT_FILE, "the SVG uses a DTD, entities or external references"
        ) from exc
    except ET.ParseError as exc:
        raise DomainError(ErrorCode.CORRUPT_FILE, f"the SVG is not valid XML: {exc}") from exc
    if _local(root.tag) != "svg":
        raise DomainError(ErrorCode.CORRUPT_FILE, "the file is not an SVG document")
    _clean(root)
    return _serialize(root), root


_XML_NS = "http://www.w3.org/XML/1998/namespace"


def _serialize(root: ET.Element) -> bytes:
    """Iterative serializer with a document-local prefix map (``ET.tostring`` is recursive and
    needs the process-global ``register_namespace`` to keep readable prefixes)."""
    uris: dict[str, str] = {}  # namespace uri -> prefix ("" = default)
    if root.tag.startswith("{"):
        uris[root.tag[1:].split("}", 1)[0]] = ""
    uris.setdefault(XLINK_NS, "xlink")
    uris[_XML_NS] = "xml"
    for el in root.iter():
        for name in (el.tag, *el.attrib):
            if isinstance(name, str) and name.startswith("{"):
                uri = name[1:].split("}", 1)[0]
                if uri not in uris:
                    uris[uri] = f"ns{len(uris)}"

    def qname(name: str) -> str:
        if not name.startswith("{"):
            return name
        uri, local = name[1:].split("}", 1)
        prefix = uris[uri]
        return f"{prefix}:{local}" if prefix else local

    decls = "".join(
        f" xmlns{':' + p if p else ''}={quoteattr(u)}" for u, p in uris.items() if p != "xml"
    )
    out: list[str] = ['<?xml version="1.0" encoding="utf-8"?>\n']
    stack: list[tuple[ET.Element | str, bool]] = [(root, True)]
    while stack:
        item, first = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue
        attrs = "".join(f" {qname(k)}={quoteattr(v)}" for k, v in item.attrib.items())
        tag = qname(item.tag)
        out.append(f"<{tag}{decls if first else ''}{attrs}")
        if len(item) == 0 and not item.text:
            out.append("/>")
        else:
            out.append(">" + escape(item.text or ""))
            stack.append((f"</{tag}>", False))
            for child in reversed(list(item)):
                stack.append((escape(child.tail or ""), False))
                stack.append((child, False))
            continue
    return "".join(out).encode("utf-8")


def _is_href_animation(el: ET.Element) -> bool:
    if _local(el.tag) not in _ANIMATION_TAGS:
        return False
    target = (el.get("attributeName") or "").strip().lower()
    return target.rsplit(":", 1)[-1] == "href"


def _clean(root: ET.Element) -> None:
    """Strip active / external content in place. Iterative (no recursion limit to hit) and
    bounded in depth and in embedded ``data:`` payload."""
    data_bytes = 0
    stack: list[tuple[ET.Element, int]] = [(root, 1)]
    while stack:
        el, depth = stack.pop()
        if depth > _MAX_DEPTH:
            raise DomainError(
                ErrorCode.CORRUPT_FILE, f"the SVG is nested more than {_MAX_DEPTH} levels deep"
            )
        for child in list(el):
            if _local(child.tag) in _DROP_TAGS or _is_href_animation(child):
                el.remove(child)
            else:
                stack.append((child, depth + 1))
        for key in list(el.attrib):
            val = el.attrib[key]
            name = _local(key)
            if name == "href":
                if not _safe_href(val):
                    del el.attrib[key]
                    continue
                if not val.strip().startswith("#"):
                    data_bytes += len(val)
                    if data_bytes > _MAX_DATA_URI_BYTES:
                        raise DomainError(
                            ErrorCode.CORRUPT_FILE,
                            "the SVG embeds more than "
                            f"{_MAX_DATA_URI_BYTES // (1024 * 1024)} MB of images",
                        )
            elif name.startswith("on") or "\\" in val:  # handlers; CSS escapes
                del el.attrib[key]
            elif name == "style":
                el.attrib[key] = _clean_css(val)
            elif "url(" in val.lower():
                el.attrib[key] = _URL_REF.sub("none", val)
        if _local(el.tag) == "style":
            text = el.text or ""
            el.text = None if "\\" in text else _clean_css(text)
            for sub in el:  # a <style> has no legitimate children
                el.remove(sub)


def _length_mm(value: str | None) -> float | None:
    """A length in mm if it carries a physical unit, else ``None``."""
    if not value:
        return None
    m = _LENGTH.match(value)
    if not m or m.group(2).lower() not in _UNIT_MM:
        return None
    mm = float(m.group(1)) * _UNIT_MM[m.group(2).lower()]
    return mm if mm > 0 else None


def _px_mm(value: str | None) -> float | None:
    """A length written with an explicit ``px`` unit, in mm at 96 px/in (else ``None``)."""
    if not value:
        return None
    m = _LENGTH.match(value)
    if not m or m.group(2).lower() != "px":
        return None
    mm = float(m.group(1)) * 25.4 / 96
    return mm if mm > 0 else None


def has_physical_size(root: ET.Element) -> bool:
    """Real units on both axes, or explicit ``px`` that is a recognized standard size at 96 px/in.

    Unitless / other ``px`` sizes carry no physical meaning (they are assumed to be A0).
    """
    if _length_mm(root.get("width")) is not None and _length_mm(root.get("height")) is not None:
        return True
    w, h = _px_mm(root.get("width")), _px_mm(root.get("height"))
    return w is not None and h is not None and _standard_match(w, h) is not None


class SvgConverter:
    id = "svg"
    handles = frozenset({DocFormat.SVG, DocFormat.SVGZ})

    def available(self) -> bool:
        # the browser build has no subprocesses: MuPDF opens SVG itself
        return tool_available("rsvg-convert") or tool_available("inkscape") or is_web()

    def convert(self, src: Path, work: Path, ctx: ConvertContext) -> CanonicalDocument:
        fmt = ctx.format or DocFormat.SVG
        scratch = ctx.scratch_dir(work)
        env = tool_env(ctx, scratch)
        clean, root = sanitize_svg(read_svg_bytes(src, fmt is DocFormat.SVGZ))
        folder = fresh_dir(scratch / "svg")
        staged = folder / "input.svg"
        staged.write_bytes(clean)
        pdf = folder / "output.pdf"
        ctx.report("convert", 5, "ingest.convert", tool="rsvg")
        self._render(staged, pdf, scratch, ctx, env, root)

        assumed = not has_physical_size(root)
        if assumed:
            self._fit_assumed(pdf, work)
        canon = pdf_to_canonical(pdf, work, ctx, fmt)
        for page in canon.pages:
            page.size_assumed = assumed
        return canon

    @staticmethod
    def _render_mupdf(staged: Path, pdf: Path, root: ET.Element) -> None:
        """MuPDF renders the (already sanitized) SVG; it reads ``px`` / unitless as points."""
        try:
            with pymupdf.open(staged, filetype="svg") as doc:
                pdf.write_bytes(doc.convert_to_pdf())
        except Exception as exc:
            raise DomainError(ErrorCode.CONVERSION_FAILED, f"cannot render the SVG: {exc}") from exc
        if _length_mm(root.get("width")) is None or _length_mm(root.get("height")) is None:
            rescale_pdf_pages(pdf, 72 / 96)  # CSS px (96 per inch), like rsvg-convert

    @staticmethod
    def _render(
        staged: Path,
        pdf: Path,
        scratch: Path,
        ctx: ConvertContext,
        env: dict[str, str],
        root: ET.Element,
    ) -> None:
        if is_web() and not (tool_available("rsvg-convert") or tool_available("inkscape")):
            SvgConverter._render_mupdf(staged, pdf, root)
            return
        common = {
            "scratch": scratch,
            "timeout_s": ctx.config.job_timeout_s,
            "memory_mb": ctx.config.job_memory_mb,
            "env": env,
        }
        failure: DomainError | None = None
        if tool_available("rsvg-convert"):
            try:
                run_tool(
                    [
                        "rsvg-convert",
                        "-f",
                        "pdf",
                        "--dpi-x=96",
                        "--dpi-y=96",
                        "-o",
                        pdf,
                        staged,
                    ],
                    **common,  # type: ignore[arg-type]
                )
                if pdf.is_file() and pdf.stat().st_size > 0:
                    return
            except DomainError as exc:
                if exc.code is ErrorCode.CONVERSION_TIMEOUT:
                    raise
                failure = exc
        if not tool_available("inkscape"):
            raise failure or DomainError(ErrorCode.CONVERSION_FAILED, "no SVG renderer available")
        ctx.report("convert", 10, "ingest.convert", tool="inkscape")
        pdf.unlink(missing_ok=True)
        run_tool(
            ["inkscape", staged, "--export-type=pdf", f"--export-filename={pdf}"],
            **common,  # type: ignore[arg-type]
        )

    @staticmethod
    def _fit_assumed(pdf: Path, work: Path) -> None:
        """Scale the (px-sized) PDF so it fits A0 in its own orientation; remember its CSS px."""
        with pymupdf.open(pdf) as doc:
            if doc.page_count < 1:
                raise DomainError(ErrorCode.CORRUPT_FILE, "the SVG has no drawable area")
            rect = doc.load_page(0).rect
        px = (rect.width * 96 / 72, rect.height * 96 / 72)
        if not (px[0] > 0 and px[1] > 0):
            raise DomainError(ErrorCode.CORRUPT_FILE, "the SVG has no drawable area")
        if min(px) < _MIN_PX or not all(math.isfinite(v) for v in px):
            raise DomainError(ErrorCode.CORRUPT_FILE, "the SVG has no drawable area")
        mm_per_px = fit_to_a0_mm_per_px(px[0], px[1])
        rescale_pdf_pages(pdf, mm_per_px * 96 / 25.4)
        (work / NATIVE_SIZE_FILE).write_text(
            json.dumps({"px": list(px), "assumed_paper": ASSUMED_PAPER_ID}), encoding="utf-8"
        )
