"""preview_service.py — podgląd plików w przeglądarce.

Dwa tryby:
  • pliki, które przeglądarka pokaże sama (obrazy, PDF, wideo, audio, tekst/kod) —
    tu tylko tabela rozszerzeń → rodzaj (`preview_kind`) i typ MIME;
  • dokumenty, których przeglądarka nie otworzy (Word, PowerPoint, Excel,
    OpenDocument, CSV/TSV, notebooki Jupyter, RTF) — renderujemy je po stronie
    serwera do BEZPIECZNEGO, samodzielnego HTML-a (`render_document`).
    Stare binarne DOC/XLS/PPT i pokrewne konwertujemy do PDF przez LibreOffice,
    ale tylko jeśli `soffice` jest zainstalowany (patrz Dockerfile, ARG INSTALL_LIBREOFFICE).

Bezpieczeństwo: pliki pochodzą od użytkowników, więc
  • cała treść jest escapowana, do HTML-a trafiają tylko znaczniki budowane tutaj,
    a obrazy wyłącznie jako data: URI rastrowych formatów;
  • linki z dokumentów tylko http(s)/mailto, z rel=noopener;
  • parsery XML korzystają z zabezpieczeń doc_extract (limit rozmiaru po
    rozpakowaniu, odrzucanie <!ENTITY>);
  • odpowiedź niesie CSP `sandbox` (brak skryptów), a front osadza ją w <iframe sandbox>;
  • limity: rozmiar pliku, rozmiar wynikowego HTML-a, liczba wierszy/kolumn arkusza.
"""
import base64
import csv
import hashlib
import html
import io
import json
import logging
import mimetypes
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

from services import doc_extract as dx
from services.errors import AIError

logger = logging.getLogger(__name__)

# --- rodzaje plików --------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".jfif", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif", ".apng"}
VIDEO_EXTS = {".mp4", ".webm", ".ogv", ".ogg", ".m4v", ".mov"}
AUDIO_EXTS = {".mp3", ".wav", ".oga", ".m4a", ".flac", ".aac", ".opus", ".weba"}
PDF_EXTS = {".pdf"}
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".ini", ".cfg", ".conf", ".toml", ".yaml", ".yml",
    ".json", ".jsonl", ".xml", ".html", ".htm", ".css", ".scss", ".less", ".tex", ".bib", ".sql",
    ".py", ".pyi", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".vue", ".svelte", ".java", ".kt", ".scala",
    ".c", ".h", ".cpp", ".cc", ".hpp", ".cs", ".go", ".rs", ".swift", ".php", ".rb", ".pl", ".lua",
    ".r", ".m", ".dart", ".sh", ".bash", ".zsh", ".bat", ".ps1", ".asm", ".s", ".v", ".vhd", ".sv",
    ".gradle", ".properties", ".cmake", ".makefile", ".dockerfile", ".gitignore", ".diff", ".patch",
    ".tsv_raw", ".srt", ".vtt", ".nfo",
}
# renderowane po stronie serwera do HTML (czysty Python)
OOXML_FAMILIES = {
    ".docx": "docx", ".docm": "docx", ".dotx": "docx", ".dotm": "docx",
    ".pptx": "pptx", ".pptm": "pptx", ".ppsx": "pptx", ".potx": "pptx",
    ".xlsx": "xlsx", ".xlsm": "xlsx", ".xltx": "xlsx", ".xltm": "xlsx",
}
ODF_FAMILIES = {".odt": "odt", ".ott": "odt", ".ods": "ods", ".ots": "ods", ".odp": "odp", ".otp": "odp"}
OTHER_RENDERED = {".csv": "csv", ".tsv": "csv", ".ipynb": "ipynb", ".rtf": "rtf"}
RENDERED_FAMILIES = {**OOXML_FAMILIES, **ODF_FAMILIES, **OTHER_RENDERED}
# stare / egzotyczne formaty — tylko przez LibreOffice (→ PDF)
LIBREOFFICE_ONLY = {".doc", ".dot", ".xls", ".xlt", ".ppt", ".pps", ".pot", ".odg", ".wps", ".wpd", ".vsdx"}

MAX_PREVIEW_BYTES = 30 * 1024 * 1024   # większych plików nie renderujemy
MAX_HTML_BYTES = 8 * 1024 * 1024        # limit wynikowego HTML-a (razem z obrazami)
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_SHEET_ROWS = 500
MAX_SHEET_COLS = 50
MAX_CSV_ROWS = 1000
SOFFICE_TIMEOUT = 90

_MIME_FALLBACK = {
    ".avif": "image/avif", ".apng": "image/apng", ".jfif": "image/jpeg", ".ico": "image/x-icon",
    ".m4a": "audio/mp4", ".flac": "audio/flac", ".aac": "audio/aac", ".opus": "audio/ogg",
    ".weba": "audio/webm", ".ogv": "video/ogg", ".m4v": "video/mp4", ".mov": "video/quicktime",
}


def _ext(name: str | None) -> str:
    return os.path.splitext(name or "")[1].lower()


def has_libreoffice() -> bool:
    return shutil.which("soffice") is not None


def preview_kind(filename: str | None) -> str | None:
    """'image' | 'pdf' | 'video' | 'audio' | 'text' | 'office' | None.
    'office' = dokument renderowany po stronie serwera (endpoint preview-doc)."""
    ext = _ext(filename)
    if ext in IMAGE_EXTS:
        return "image"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in TEXT_EXTS:
        return "text"
    if ext in RENDERED_FAMILIES or (ext in LIBREOFFICE_ONLY and has_libreoffice()):
        return "office"
    return None


def mimetype_for(filename: str | None) -> str | None:
    """Typ MIME dla rodzajów serwowanych wprost (image/pdf/video/audio/text)."""
    kind = preview_kind(filename)
    if kind in (None, "office"):
        return None
    if kind == "text":
        return "text/plain"
    ext = _ext(filename)
    return _MIME_FALLBACK.get(ext) or mimetypes.types_map.get(ext) or "application/octet-stream"


def can_render(filename: str | None) -> bool:
    return preview_kind(filename) == "office"


class PreviewError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message, self.status = message, status


class _Full(Exception):
    """Wynikowy HTML osiągnął limit."""


class Out:
    """Bufor wyniku z limitem rozmiaru; renderery dopisują kawałki w trakcie parsowania,
    więc po przekroczeniu limitu zostaje to, co zdążyło się wyrenderować."""

    def __init__(self, limit: int | None = None):
        self.parts: list[str] = []
        self.size = 0
        self.limit = limit or MAX_HTML_BYTES  # czytane w momencie użycia (da się podmienić w testach)
        self.truncated = False

    def add(self, s: str) -> None:
        if self.size + len(s) > self.limit:
            self.truncated = True
            raise _Full()
        self.parts.append(s)
        self.size += len(s)

    def image(self, data: bytes, ext: str, style: str = "") -> None:
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}.get(ext.lower())
        if not mime or len(data) > MAX_IMAGE_BYTES:
            return
        b64 = base64.b64encode(data).decode("ascii")
        self.add(f'<img alt="" src="data:{mime};base64,{b64}"{style}>')


def e(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def _safe_href(url: str | None) -> str | None:
    if url and re.match(r"^(https?://|mailto:)", url.strip(), re.I):
        return url.strip()
    return None


def _color(val: str | None) -> str | None:
    return f"#{val}" if val and re.fullmatch(r"[0-9a-fA-F]{6}", val) else None


# ---------------------------------------------------------------------------
# OOXML — wspólne
# ---------------------------------------------------------------------------

_W = dx._W
_A = dx._A
_P = dx._P
_R = dx._R
_S = dx._S
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _flag(rpr, tag: str) -> bool:
    if rpr is None:
        return False
    el = rpr.find(tag)
    return el is not None and el.get(_W + "val", "true") not in ("0", "false", "off", "none")


def _zip_image(zf: zipfile.ZipFile, path: str | None) -> tuple[bytes, str] | None:
    if not path or path not in zf.namelist():
        return None
    ext = posixpath.splitext(path)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        return None  # EMF/WMF/TIFF itp. — przeglądarka ich nie wyświetli
    try:
        info = zf.getinfo(path)
        if info.file_size > MAX_IMAGE_BYTES:
            return None
        with zf.open(info) as f:
            return f.read(MAX_IMAGE_BYTES + 1), ext
    except Exception:
        return None


def _rels_with_external(zf: zipfile.ZipFile, rels_path: str, base_dir: str) -> dict:
    """Id → ('int', ścieżka_w_zipie) albo ('ext', url)."""
    out = {}
    try:
        data = dx._read_member(zf, rels_path)
    except AIError:
        return out
    for _, el in dx._iterparse(data):
        if el.tag == _REL_NS + "Relationship" or el.tag == "Relationship":
            rid, target = el.get("Id"), el.get("Target")
            if not (rid and target):
                continue
            if el.get("TargetMode") == "External":
                out[rid] = ("ext", target)
            elif target.startswith("/"):
                out[rid] = ("int", target.lstrip("/"))
            else:
                out[rid] = ("int", posixpath.normpath(posixpath.join(base_dir, target)))
    return out


def _ordinal(n: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        s, k = "", n
        while k > 0:
            k, r = divmod(k - 1, 26)
            s = chr(97 + r) + s
        return (s.upper() if fmt == "upperLetter" else s) + "."
    if fmt in ("lowerRoman", "upperRoman"):
        vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
        s, k = "", min(n, 3999)
        for v, sym in vals:
            while k >= v:
                s, k = s + sym, k - v
        return (s.upper() if fmt == "upperRoman" else s) + "."
    return f"{n}."


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

class _DocxCtx:
    def __init__(self, zf, out):
        self.zf, self.out = zf, out
        self.styles: dict[str, str] = {}
        self.numfmt: dict[tuple[str, str], str] = {}   # (numId, ilvl) -> numFmt
        self.rels = _rels_with_external(zf, "word/_rels/document.xml.rels", "word")
        self.counters: dict[tuple[str, int], int] = {}
        self._load_styles()
        self._load_numbering()

    def _load_styles(self):
        try:
            data = dx._read_member(self.zf, "word/styles.xml")
        except AIError:
            return
        for _, el in dx._iterparse(data):
            if el.tag == _W + "style":
                sid, name = el.get(_W + "styleId"), el.find(_W + "name")
                if sid and name is not None:
                    self.styles[sid] = (name.get(_W + "val") or "").lower()

    def _load_numbering(self):
        try:
            root = ET.fromstring(dx._read_member(self.zf, "word/numbering.xml"))
        except (AIError, ET.ParseError):
            return
        abstract = {}
        for an in root.findall(_W + "abstractNum"):
            lv = {}
            for lvl in an.findall(_W + "lvl"):
                fmt = lvl.find(_W + "numFmt")
                lv[lvl.get(_W + "ilvl")] = fmt.get(_W + "val") if fmt is not None else "decimal"
            abstract[an.get(_W + "abstractNumId")] = lv
        for num in root.findall(_W + "num"):
            aid = num.find(_W + "abstractNumId")
            if aid is not None:
                for ilvl, fmt in abstract.get(aid.get(_W + "val"), {}).items():
                    self.numfmt[(num.get(_W + "numId"), ilvl)] = fmt


def _docx_image(el, ctx: _DocxCtx):
    """<w:drawing> / <w:pict> → <img>."""
    rid, width = None, None
    for n in el.iter():
        if n.tag == _A + "blip":
            rid = n.get(_R + "embed") or n.get(_R + "link")
        elif n.tag.endswith("}imagedata"):
            rid = n.get(_R + "id")
        elif n.tag == _WP + "extent" and n.get("cx", "").isdigit():
            width = min(int(n.get("cx")) // 9525, 800)
    rel = ctx.rels.get(rid or "")
    if rel and rel[0] == "int":
        got = _zip_image(ctx.zf, rel[1])
        if got:
            style = f' style="max-width:100%;height:auto;width:{width}px"' if width else ' style="max-width:100%;height:auto"'
            ctx.out.image(got[0], got[1], style)


def _docx_run(r, ctx: _DocxCtx) -> None:
    rpr = r.find(_W + "rPr")
    open_t, close_t = [], []

    def wrap(tag, css=None):
        attr = f' style="{css}"' if css else ""
        open_t.append(f"<{tag}{attr}>")
        close_t.insert(0, f"</{tag}>")

    if _flag(rpr, _W + "b"):
        wrap("b")
    if _flag(rpr, _W + "i"):
        wrap("i")
    if rpr is not None:
        u = rpr.find(_W + "u")
        if u is not None and u.get(_W + "val") not in (None, "none"):
            wrap("u")
        if _flag(rpr, _W + "strike"):
            wrap("s")
        va = rpr.find(_W + "vertAlign")
        if va is not None and va.get(_W + "val") in ("superscript", "subscript"):
            wrap("sup" if va.get(_W + "val") == "superscript" else "sub")
        col = rpr.find(_W + "color")
        c = _color(col.get(_W + "val")) if col is not None else None
        if c and c.lower() not in ("#000000", "#ffffff"):
            wrap("span", f"color:{c}")

    ctx.out.add("".join(open_t))
    for ch in r:
        if ch.tag == _W + "t":
            ctx.out.add(e(ch.text))
        elif ch.tag == _W + "tab":
            ctx.out.add("&emsp;")
        elif ch.tag == _W + "br":
            ctx.out.add('<hr class="pb">' if ch.get(_W + "type") == "page" else "<br>")
        elif ch.tag == _W + "cr":
            ctx.out.add("<br>")
        elif ch.tag == _W + "noBreakHyphen":
            ctx.out.add("\u2011")
        elif ch.tag in (_W + "drawing", _W + "pict"):
            _docx_image(ch, ctx)
    ctx.out.add("".join(close_t))


def _docx_inline(parent, ctx: _DocxCtx) -> None:
    for ch in parent:
        if ch.tag == _W + "r":
            _docx_run(ch, ctx)
        elif ch.tag == _W + "hyperlink":
            rel = ctx.rels.get(ch.get(_R + "id") or "")
            href = _safe_href(rel[1]) if rel and rel[0] == "ext" else None
            if href:
                ctx.out.add(f'<a href="{e(href)}" target="_blank" rel="noopener noreferrer">')
                _docx_inline(ch, ctx)
                ctx.out.add("</a>")
            else:
                _docx_inline(ch, ctx)
        elif ch.tag in (_W + "ins", _W + "smartTag", _W + "fldSimple", _W + "sdt", _W + "sdtContent"):
            _docx_inline(ch, ctx)


def _docx_paragraph(p, ctx: _DocxCtx) -> None:
    ppr = p.find(_W + "pPr")
    sname, align, num = "", None, None
    if ppr is not None:
        ps = ppr.find(_W + "pStyle")
        if ps is not None:
            sname = ctx.styles.get(ps.get(_W + "val"), ps.get(_W + "val", "")).lower()
        jc = ppr.find(_W + "jc")
        if jc is not None:
            align = {"center": "center", "right": "right", "both": "justify", "end": "right"}.get(jc.get(_W + "val"))
        npr = ppr.find(_W + "numPr")
        if npr is not None:
            nid, lvl = npr.find(_W + "numId"), npr.find(_W + "ilvl")
            if nid is not None and nid.get(_W + "val") not in (None, "0"):
                num = (nid.get(_W + "val"), lvl.get(_W + "val") if lvl is not None else "0")

    style = f' style="text-align:{align}"' if align else ""
    m = re.match(r"(?:heading|nagłówek)\s*(\d)", sname)
    if sname in ("title", "tytuł"):
        tag = "h1"
    elif m:
        tag = f"h{min(int(m.group(1)), 6)}"
    elif sname in ("subtitle", "podtytuł"):
        tag = "h2"
    else:
        tag = "p"

    if num:
        nid, ilvl = num
        lvl = int(ilvl) if ilvl.isdigit() else 0
        fmt = ctx.numfmt.get((nid, ilvl), "decimal")
        if fmt == "bullet":
            label = ("•", "◦", "▪")[lvl % 3]
        else:
            ctx.counters[(nid, lvl)] = ctx.counters.get((nid, lvl), 0) + 1
            for k in [k for k in ctx.counters if k[0] == nid and k[1] > lvl]:
                del ctx.counters[k]
            label = _ordinal(ctx.counters[(nid, lvl)], fmt)
        ctx.out.add(f'<p class="li" style="margin-left:{(lvl + 1) * 1.6}em"><span class="n">{e(label)}</span>')
    else:
        ctx.out.add(f"<{tag}{style}>")
    before = ctx.out.size
    _docx_inline(p, ctx)
    if ctx.out.size == before:
        ctx.out.add("&nbsp;")
    ctx.out.add("</p>" if num else f"</{tag}>")


def _docx_table(tbl, ctx: _DocxCtx) -> None:
    ctx.out.add('<table class="dt">')
    for tr in tbl.findall(_W + "tr"):
        ctx.out.add("<tr>")
        for tc in tr.findall(_W + "tc"):
            tcpr = tc.find(_W + "tcPr")
            span = 1
            if tcpr is not None:
                gs = tcpr.find(_W + "gridSpan")
                if gs is not None and gs.get(_W + "val", "").isdigit():
                    span = min(int(gs.get(_W + "val")), 50)
            ctx.out.add(f'<td colspan="{span}">' if span > 1 else "<td>")
            _docx_blocks(tc, ctx)
            ctx.out.add("</td>")
        ctx.out.add("</tr>")
    ctx.out.add("</table>")


def _docx_blocks(parent, ctx: _DocxCtx) -> None:
    for ch in parent:
        if ch.tag == _W + "p":
            _docx_paragraph(ch, ctx)
        elif ch.tag == _W + "tbl":
            _docx_table(ch, ctx)
        elif ch.tag in (_W + "sdt", _W + "sdtContent"):
            _docx_blocks(ch, ctx)


def _render_docx(path: str, out: Out) -> None:
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(dx._read_member(zf, "word/document.xml"))
        body = root.find(_W + "body")
        if body is None:
            raise PreviewError("Dokument jest pusty.")
        out.add('<article class="paper">')
        _docx_blocks(body, _DocxCtx(zf, out))
        out.add("</article>")


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------

def _pptx_text_runs(p, out: Out) -> None:
    for ch in p:
        if ch.tag == _A + "r":
            rpr = ch.find(_A + "rPr")
            t = ch.find(_A + "t")
            txt = e(t.text if t is not None else "")
            if rpr is not None:
                if rpr.get("b") == "1":
                    txt = f"<b>{txt}</b>"
                if rpr.get("i") == "1":
                    txt = f"<i>{txt}</i>"
                if rpr.get("u") not in (None, "none"):
                    txt = f"<u>{txt}</u>"
            out.add(txt)
        elif ch.tag == _A + "br":
            out.add("<br>")
        elif ch.tag == _A + "fld":
            t = ch.find(_A + "t")
            out.add(e(t.text if t is not None else ""))


def _pptx_para_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(_A + "t")).strip()


def _pptx_shapes(parent, zf, rels: dict, out: Out) -> None:
    for el in parent:
        if el.tag == _P + "sp":
            ph = el.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
            phtype = ph.get("type") if ph is not None else None
            if phtype in ("sldNum", "dt", "ftr", "hdr"):
                continue
            tx = el.find(_P + "txBody")
            if tx is None:
                continue
            paras = [p for p in tx.findall(_A + "p") if _pptx_para_text(p)]
            if not paras:
                continue
            title = phtype in ("title", "ctrTitle")
            out.add('<div class="shape">')
            for p in paras:
                ppr = p.find(_A + "pPr")
                lvl = int(ppr.get("lvl", "0")) if ppr is not None and ppr.get("lvl", "0").isdigit() else 0
                bullet = False
                if ppr is not None and ppr.find(_A + "buNone") is None:
                    bullet = ppr.find(_A + "buChar") is not None or ppr.find(_A + "buAutoNum") is not None
                if not bullet and phtype == "body" and (ppr is None or ppr.find(_A + "buNone") is None):
                    bullet = True
                if title:
                    out.add('<h2 class="st">')
                    _pptx_text_runs(p, out)
                    out.add("</h2>")
                elif bullet:
                    out.add(f'<p class="li" style="margin-left:{(lvl + 1) * 1.4}em"><span class="n">•</span>')
                    _pptx_text_runs(p, out)
                    out.add("</p>")
                else:
                    out.add("<p>")
                    _pptx_text_runs(p, out)
                    out.add("</p>")
            out.add("</div>")
        elif el.tag == _P + "pic":
            blip = next(el.iter(_A + "blip"), None)
            target = rels.get(blip.get(_R + "embed")) if blip is not None else None
            got = _zip_image(zf, target)
            if got:
                out.add('<div class="shape">')
                out.image(got[0], got[1], ' style="max-width:100%;height:auto"')
                out.add("</div>")
        elif el.tag == _P + "graphicFrame":
            tbl = next(el.iter(_A + "tbl"), None)
            if tbl is not None:
                out.add('<table class="dt">')
                for tr in tbl.findall(_A + "tr"):
                    out.add("<tr>")
                    for tc in tr.findall(_A + "tc"):
                        if tc.get("hMerge") == "1" or tc.get("vMerge") == "1":
                            continue
                        span = tc.get("gridSpan", "1")
                        cell = "<br>".join(e(_pptx_para_text(p)) for p in tc.iter(_A + "p") if _pptx_para_text(p))
                        out.add(f'<td colspan="{int(span)}">{cell}</td>' if span.isdigit() and int(span) > 1
                                else f"<td>{cell}</td>")
                    out.add("</tr>")
                out.add("</table>")
        elif el.tag == _P + "grpSp":
            _pptx_shapes(el, zf, rels, out)


def _pptx_notes(zf, rels: dict) -> str:
    target = next((v for v in rels.values() if "/notesSlides/" in v), None)
    if not target or target not in zf.namelist():
        return ""
    try:
        root = ET.fromstring(dx._read_member(zf, target))
    except (AIError, ET.ParseError):
        return ""
    lines = []
    for sp in root.iter(_P + "sp"):
        ph = sp.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
        if ph is not None and ph.get("type") == "body":
            lines += [_pptx_para_text(p) for p in sp.iter(_A + "p") if _pptx_para_text(p)]
    return "\n".join(lines)


def _render_pptx(path: str, out: Out) -> None:
    with zipfile.ZipFile(path) as zf:
        slides = dx._slide_order(zf)
        if not slides:
            raise PreviewError("Nie znaleziono slajdów w prezentacji.")
        out.add(f'<div class="deck-info">{len(slides)} slajdów — podgląd treści (układ i animacje pominięte)</div>')
        for i, name in enumerate(slides, start=1):
            root = ET.fromstring(dx._read_member(zf, name))
            rels = dx._rels(zf, posixpath.join(posixpath.dirname(name), "_rels",
                                               posixpath.basename(name) + ".rels"), posixpath.dirname(name))
            out.add(f'<section class="slide"><div class="sn">Slajd {i}</div>')
            tree = root.find(f"{_P}cSld/{_P}spTree")
            if tree is not None:
                _pptx_shapes(tree, zf, rels, out)
            notes = _pptx_notes(zf, rels)
            if notes:
                out.add(f'<div class="notes"><b>Notatki:</b> {e(notes[:2000])}</div>')
            out.add("</section>")


# ---------------------------------------------------------------------------
# XLSX / arkusze
# ---------------------------------------------------------------------------

def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Za-z]+", ref or "A").group(0).upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _col_letters(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _fmt_num(s: str) -> str:
    try:
        f = float(s)
    except ValueError:
        return s
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return "%.12g" % f


_BUILTIN_DATE_IDS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))


def _is_date_format(fid: int, code: str | None) -> bool:
    if code is None:
        return fid in _BUILTIN_DATE_IDS
    c = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code).lower()
    return bool(re.search(r"[dyh]", c) or ("m" in c and not re.search(r"[0#?]", c)) or re.search(r"(?<![0#?])ss?", c))


def _xlsx_styles(zf) -> list[tuple[str, str | None]]:
    """Dla każdego indeksu stylu komórki: ('date'|'pct'|'num', kod_formatu)."""
    try:
        root = ET.fromstring(dx._read_member(zf, "xl/styles.xml"))
    except (AIError, ET.ParseError):
        return []
    codes = {int(n.get("numFmtId")): n.get("formatCode") for n in root.iter(_S + "numFmt")
             if (n.get("numFmtId") or "").isdigit()}
    out = []
    xfs = root.find(_S + "cellXfs")
    for xf in (xfs if xfs is not None else []):
        fid = int(xf.get("numFmtId", "0")) if xf.get("numFmtId", "0").isdigit() else 0
        code = codes.get(fid)
        if _is_date_format(fid, code):
            out.append(("date", code))
        elif fid in (9, 10) or (code and "%" in code):
            out.append(("pct", code))
        else:
            out.append(("num", code))
    return out


def _format_value(raw: str, kind: str) -> str:
    if kind == "date":
        try:
            f = float(raw)
            dt = datetime(1899, 12, 30) + timedelta(days=f)
            return dt.strftime("%Y-%m-%d %H:%M" if f != int(f) else "%Y-%m-%d")
        except (ValueError, OverflowError):
            return raw
    if kind == "pct":
        try:
            return f"{float(raw) * 100:.2f}".rstrip("0").rstrip(".") + "%"
        except ValueError:
            return raw
    return _fmt_num(raw)


def _xlsx_sheet_grid(data: bytes, shared: list[str], styles: list) -> tuple[list[tuple[int, dict]], bool]:
    rows, truncated = [], False
    for _, el in dx._iterparse(data):
        if el.tag != _S + "row":
            continue
        cells = {}
        for c in el.findall(_S + "c"):
            col = _col_index(c.get("r", ""))
            if col >= MAX_SHEET_COLS:
                truncated = True
                continue
            t, v = c.get("t"), c.find(_S + "v")
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(_S + "t"))
            elif v is None or v.text is None:
                continue
            elif t == "s":
                try:
                    val = shared[int(v.text)]
                except (ValueError, IndexError):
                    continue
            elif t == "b":
                val = "TRUE" if v.text.strip() == "1" else "FALSE"
            elif t in ("str", "e"):
                val = v.text
            else:
                sidx = c.get("s")
                kind = styles[int(sidx)][0] if sidx and sidx.isdigit() and int(sidx) < len(styles) else "num"
                val = _format_value(v.text, kind)
            if val != "":
                cells[col] = val
        rnum = int(el.get("r")) if (el.get("r") or "").isdigit() else (rows[-1][0] + 1 if rows else 1)
        el.clear()
        if cells:
            rows.append((rnum, cells))
            if len(rows) >= MAX_SHEET_ROWS:
                truncated = True
                break
    return rows, truncated


def _sheet_html(name: str, rows: list[tuple[int, dict]], truncated: bool, out: Out, sid: str) -> None:
    out.add(f'<section class="sheet" id="{sid}"><h3>{e(name)}</h3>')
    if not rows:
        out.add('<p class="muted">Arkusz jest pusty.</p></section>')
        return
    ncols = max(max(c) for _, c in rows) + 1
    out.add('<div class="scroll"><table class="grid"><thead><tr><th></th>')
    out.add("".join(f"<th>{_col_letters(i)}</th>" for i in range(ncols)))
    out.add("</tr></thead><tbody>")
    for rnum, cells in rows:
        tds = "".join(f"<td>{e(cells.get(i, ''))}</td>" for i in range(ncols))
        out.add(f'<tr><th>{rnum}</th>{tds}</tr>')
    out.add("</tbody></table></div>")
    if truncated:
        out.add(f'<p class="muted">Pokazano pierwsze {MAX_SHEET_ROWS} wierszy / {MAX_SHEET_COLS} kolumn — '
                "pobierz plik, aby zobaczyć całość.</p>")
    out.add("</section>")


def _render_xlsx(path: str, out: Out) -> None:
    with zipfile.ZipFile(path) as zf:
        shared = dx._shared_strings(zf)
        styles = _xlsx_styles(zf)
        rels = dx._rels(zf, "xl/_rels/workbook.xml.rels", "xl")
        sheets = []
        for _, el in dx._iterparse(dx._read_member(zf, "xl/workbook.xml")):
            if el.tag == _S + "sheet" and el.get("state") not in ("hidden", "veryHidden"):
                target = rels.get(el.get(_R + "id"))
                if target:
                    sheets.append((el.get("name") or f"Arkusz{len(sheets) + 1}", target))
        if not sheets:
            raise PreviewError("Nie znaleziono arkuszy w pliku.")
        if len(sheets) > 1:
            out.add('<nav class="tabs">' + "".join(
                f'<a href="#s{i}">{e(n)}</a>' for i, (n, _) in enumerate(sheets, 1)) + "</nav>")
        for i, (name, target) in enumerate(sheets, 1):
            rows, trunc = _xlsx_sheet_grid(dx._read_member(zf, target), shared, styles)
            _sheet_html(name, rows, trunc, out, f"s{i}")


# ---------------------------------------------------------------------------
# OpenDocument
# ---------------------------------------------------------------------------

_T, _D, _TB = dx._T, dx._D, dx._TB
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def _odt_blocks(parent, out: Out) -> None:
    for ch in parent:
        if ch.tag == _T + "h":
            lvl = ch.get(_T + "outline-level", "1")
            lvl = min(int(lvl), 6) if lvl.isdigit() else 1
            out.add(f"<h{lvl}>{e(dx._odf_text(ch))}</h{lvl}>")
        elif ch.tag == _T + "p":
            txt = dx._odf_text(ch)
            out.add(f"<p>{e(txt) if txt.strip() else '&nbsp;'}</p>")
        elif ch.tag == _T + "list":
            _odt_list(ch, out, 0)
        elif ch.tag == _TB + "table":
            out.add('<table class="dt">')
            for row in ch.iter(_TB + "table-row"):
                out.add("<tr>")
                for c in row:
                    if c.tag in (_TB + "table-cell", _TB + "covered-table-cell"):
                        out.add("<td>" + "<br>".join(e(t) for t in dx._odf_paras(c) if t.strip()) + "</td>")
                out.add("</tr>")
            out.add("</table>")
        elif ch.tag in (_T + "section", _T + "table-of-content", _T + "index-body"):
            _odt_blocks(ch, out)


def _odt_list(lst, out: Out, depth: int) -> None:
    for item in lst.findall(_T + "list-item"):
        for ch in item:
            if ch.tag in (_T + "p", _T + "h"):
                out.add(f'<p class="li" style="margin-left:{(depth + 1) * 1.6}em">'
                        f'<span class="n">•</span>{e(dx._odf_text(ch))}</p>')
            elif ch.tag == _T + "list":
                _odt_list(ch, out, depth + 1)


def _odf_root(path: str):
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(dx._read_member(zf, "content.xml"))


def _render_odt(path: str, out: Out) -> None:
    root = _odf_root(path)
    body = root.find(f"{_OFFICE}body/{_OFFICE}text")
    if body is None:
        raise PreviewError("Dokument jest pusty.")
    out.add('<article class="paper">')
    _odt_blocks(body, out)
    out.add("</article>")


def _render_odp(path: str, out: Out) -> None:
    pages = list(_odf_root(path).iter(_D + "page"))
    if not pages:
        raise PreviewError("Nie znaleziono slajdów w prezentacji.")
    out.add(f'<div class="deck-info">{len(pages)} slajdów — podgląd treści</div>')
    for i, page in enumerate(pages, 1):
        out.add(f'<section class="slide"><div class="sn">Slajd {i}</div>')
        for t in dx._odf_paras(page):
            if t.strip():
                out.add(f"<p>{e(t)}</p>")
        out.add("</section>")


def _render_ods(path: str, out: Out) -> None:
    tables = list(_odf_root(path).iter(_TB + "table"))
    if not tables:
        raise PreviewError("Nie znaleziono arkuszy w pliku.")
    if len(tables) > 1:
        out.add('<nav class="tabs">' + "".join(
            f'<a href="#s{i}">{e(t.get(_TB + "name") or i)}</a>' for i, t in enumerate(tables, 1)) + "</nav>")
    for i, tbl in enumerate(tables, 1):
        rows, truncated = [], False
        rnum = 0
        for row in tbl.iter(_TB + "table-row"):
            rrep = row.get(_TB + "number-rows-repeated", "1")
            rrep = min(int(rrep), 1) if rrep.isdigit() else 1  # powtórzone wiersze to zwykle puste "wypełniacze"
            cells, col = {}, 0
            for c in row:
                if c.tag not in (_TB + "table-cell", _TB + "covered-table-cell"):
                    continue
                crep = c.get(_TB + "number-columns-repeated", "1")
                crep = int(crep) if crep.isdigit() else 1
                txt = " ".join(t for t in dx._odf_paras(c) if t.strip()).strip()
                if txt:
                    for k in range(min(crep, 20)):
                        if col + k < MAX_SHEET_COLS:
                            cells[col + k] = txt
                        else:
                            truncated = True
                col += crep
            rnum += rrep
            if cells:
                rows.append((rnum, cells))
                if len(rows) >= MAX_SHEET_ROWS:
                    truncated = True
                    break
        _sheet_html(tbl.get(_TB + "name") or f"Arkusz{i}", rows, truncated, out, f"s{i}")


# ---------------------------------------------------------------------------
# CSV / TSV, notebooki Jupyter, RTF
# ---------------------------------------------------------------------------

def _read_limited(path: str, limit: int) -> tuple[bytes, bool]:
    with open(path, "rb") as f:
        raw = f.read(limit)
        return raw, bool(f.read(1))


def _render_csv(path: str, out: Out, ext: str) -> None:
    raw, more = _read_limited(path, 2 * 1024 * 1024)
    if b"\x00" in raw[:4096]:
        raise PreviewError("To nie jest plik tekstowy.")
    text = dx._decode_text(raw)
    first = text.split("\n", 1)[0]
    delim = "\t" if ext == ".tsv" else max((",", ";", "\t", "|"), key=first.count)
    rows, truncated = [], more
    try:
        for row in csv.reader(io.StringIO(text), delimiter=delim):
            rows.append(row[:MAX_SHEET_COLS])
            if len(row) > MAX_SHEET_COLS:
                truncated = True
            if len(rows) >= MAX_CSV_ROWS:
                truncated = True
                break
    except csv.Error:
        truncated = True
    if not rows:
        raise PreviewError("Plik jest pusty.")
    out.add('<div class="scroll"><table class="grid csv"><thead><tr>')
    out.add("".join(f"<th>{e(c)}</th>" for c in rows[0]))
    out.add("</tr></thead><tbody>")
    for row in rows[1:]:
        out.add("<tr>" + "".join(f"<td>{e(c)}</td>" for c in row) + "</tr>")
    out.add("</tbody></table></div>")
    if truncated:
        out.add(f'<p class="muted">Pokazano pierwsze {MAX_CSV_ROWS} wierszy — pobierz plik, aby zobaczyć całość.</p>')


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _nb_text(src) -> str:
    return "".join(src) if isinstance(src, list) else str(src or "")


def _render_ipynb(path: str, out: Out) -> None:
    raw, more = _read_limited(path, 15 * 1024 * 1024)
    if more:
        raise PreviewError("Notebook jest zbyt duży do podglądu.", 413)
    try:
        nb = json.loads(dx._decode_text(raw))
        cells = nb["cells"] if "cells" in nb else [c for ws in nb["worksheets"] for c in ws["cells"]]
    except (ValueError, KeyError, TypeError):
        raise PreviewError("To nie jest poprawny notebook Jupyter.")
    out.add('<article class="paper nb">')
    for cell in cells[:500]:
        ctype = cell.get("cell_type")
        src = _nb_text(cell.get("source") or cell.get("input"))
        if ctype == "markdown":
            for line in src.splitlines():
                m = re.match(r"(#{1,6})\s+(.*)", line)
                out.add(f"<h{len(m.group(1))}>{e(m.group(2))}</h{len(m.group(1))}>" if m
                        else (f"<p>{e(line)}</p>" if line.strip() else ""))
        elif ctype == "code":
            out.add(f'<pre class="code">{e(src[:20000])}</pre>')
            for o in cell.get("outputs", [])[:20]:
                otype = o.get("output_type")
                if otype == "stream":
                    out.add(f'<pre class="out">{e(_nb_text(o.get("text"))[:10000])}</pre>')
                elif otype in ("execute_result", "display_data"):
                    data = o.get("data", {})
                    for mime, ext in (("image/png", ".png"), ("image/jpeg", ".jpg")):
                        if mime in data:
                            try:
                                out.image(base64.b64decode(_nb_text(data[mime]), validate=False), ext,
                                          ' style="max-width:100%;height:auto"')
                            except ValueError:
                                pass
                    if "text/plain" in data and not any(m in data for m in ("image/png", "image/jpeg")):
                        out.add(f'<pre class="out">{e(_nb_text(data["text/plain"])[:10000])}</pre>')
                elif otype == "error":
                    tb = _ANSI.sub("", "\n".join(o.get("traceback", [])))
                    out.add(f'<pre class="out err">{e(tb[:5000])}</pre>')
    out.add("</article>")


def _rtf_to_text(data: str) -> str:
    # grupy z metadanymi (fonttbl, stylesheet, pict, \*) wycinamy razem z zawartością
    depth, skip_depth, out, i = 0, None, [], 0
    while i < len(data):
        ch = data[i]
        if ch == "{":
            depth += 1
            if data[i + 1:i + 3] == "\\*" or re.match(r"\{\\(fonttbl|colortbl|stylesheet|info|pict|header|footer)", data[i:i + 14]):
                skip_depth = skip_depth or depth
        elif ch == "}":
            if skip_depth == depth:
                skip_depth = None
            depth -= 1
        elif ch == "\\":
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", data[i:])
            if m:
                word, arg = m.group(1), m.group(2)
                if not skip_depth:
                    if word in ("par", "line"):
                        out.append("\n")
                    elif word == "tab":
                        out.append("\t")
                    elif word == "u" and arg:
                        n = int(arg)
                        out.append(chr(n if n >= 0 else n + 65536))
                i += m.end()
                continue
            h = re.match(r"\\'([0-9a-fA-F]{2})", data[i:])
            if h:
                if not skip_depth:
                    out.append(bytes([int(h.group(1), 16)]).decode("cp1250", "replace"))
                i += 4
                continue
            if i + 1 < len(data) and data[i + 1] in "\\{}":
                if not skip_depth:
                    out.append(data[i + 1])
                i += 2
                continue
        elif not skip_depth and ch not in "\r\n":
            out.append(ch)
        i += 1
    return "".join(out)


def _render_rtf(path: str, out: Out) -> None:
    raw, _ = _read_limited(path, 5 * 1024 * 1024)
    if not raw.lstrip().startswith(b"{\\rtf"):
        raise PreviewError("To nie jest poprawny plik RTF.")
    out.add('<article class="paper">')
    for line in _rtf_to_text(raw.decode("latin-1")).splitlines():
        out.add(f"<p>{e(line) if line.strip() else '&nbsp;'}</p>")
    out.add("</article>")


# ---------------------------------------------------------------------------
# LibreOffice (opcjonalnie) — stare formaty → PDF
# ---------------------------------------------------------------------------

def convert_to_pdf(path: str, name: str | None, cache_dir: str | None = None,
                   cache_key: str | None = None) -> str:
    """Zwraca ścieżkę PDF-a. Z `cache_dir` wynik jest zapamiętany (klucz = cache_key)."""
    soffice = shutil.which("soffice")
    if not soffice:
        raise PreviewError("Podgląd tego formatu wymaga LibreOffice, którego nie ma na serwerze. "
                           "Zapisz plik w nowszym formacie (DOCX/XLSX/PPTX) albo pobierz go.", 415)
    cached = None
    if cache_dir and cache_key:
        os.makedirs(cache_dir, exist_ok=True)
        cached = os.path.join(cache_dir, re.sub(r"[^A-Za-z0-9._-]", "_", cache_key) + ".pdf")
        if os.path.exists(cached) and os.path.getmtime(cached) >= os.path.getmtime(path):
            return cached
    work = tempfile.mkdtemp(prefix="kolprev_")
    try:
        src = os.path.join(work, "in" + (_ext(name) or ".bin"))
        shutil.copyfile(path, src)
        try:
            subprocess.run(
                [soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", work, src],
                check=True, timeout=SOFFICE_TIMEOUT, capture_output=True,
                env={**os.environ, "HOME": work},
            )
        except (subprocess.SubprocessError, OSError):
            logger.exception("LibreOffice conversion failed for %s", name)
            raise PreviewError("Nie udało się skonwertować pliku do podglądu.")
        pdf = os.path.join(work, "in.pdf")
        if not os.path.exists(pdf):
            raise PreviewError("Nie udało się skonwertować pliku do podglądu.")
        if cached:
            shutil.move(pdf, cached)
            return cached
        keep = tempfile.NamedTemporaryFile(prefix="kolprev_", suffix=".pdf", delete=False)
        keep.close()
        shutil.move(pdf, keep.name)
        return keep.name
    finally:
        shutil.rmtree(work, ignore_errors=True)


def remove_cached(cache_dir: str, cache_key: str) -> None:
    p = os.path.join(cache_dir, re.sub(r"[^A-Za-z0-9._-]", "_", cache_key) + ".pdf")
    if os.path.exists(p):
        os.remove(p)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

_CSS = """
*{box-sizing:border-box}body{margin:0;padding:16px;background:#eef0f3;color:#1f2328;
font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.paper{max-width:860px;margin:0 auto;background:#fff;padding:40px 48px;border-radius:4px;
box-shadow:0 1px 4px rgba(0,0,0,.15);overflow-wrap:anywhere}
.paper h1,.paper h2,.paper h3,.paper h4{line-height:1.25;margin:1.1em 0 .4em}
.paper p{margin:.45em 0}.li{display:flex;gap:.5em}.li .n{flex:none;min-width:1.3em;text-align:right}
a{color:#0b63ce}img{display:block;margin:8px 0}hr.pb{border:0;border-top:1px dashed #bbb;margin:20px 0}
table.dt{border-collapse:collapse;margin:10px 0;max-width:100%}table.dt td{border:1px solid #c8ccd1;padding:4px 8px;vertical-align:top}
.scroll{overflow:auto;max-width:100%;background:#fff;border:1px solid #c8ccd1;border-radius:4px}
table.grid{border-collapse:collapse;font-size:13px;white-space:nowrap}
table.grid td,table.grid th{border:1px solid #dde0e4;padding:3px 8px}
table.grid thead th,table.grid tbody th{background:#f3f4f6;color:#555;font-weight:600;text-align:center;position:sticky}
table.grid thead th{top:0}table.grid tbody th{left:0}table.csv thead th{color:#1f2328;text-align:left}
.sheet{max-width:1200px;margin:0 auto 20px}.sheet h3{margin:6px 2px}
.tabs{max-width:1200px;margin:0 auto 14px;display:flex;gap:6px;flex-wrap:wrap}
.tabs a{background:#fff;border:1px solid #c8ccd1;border-radius:14px;padding:3px 12px;text-decoration:none;font-size:13px}
.muted,.deck-info{color:#6b7280;font-size:13px;max-width:860px;margin:6px auto}
.slide{max-width:860px;margin:0 auto 18px;background:#fff;border-radius:6px;padding:26px 34px 22px;
box-shadow:0 1px 4px rgba(0,0,0,.15);position:relative;overflow-wrap:anywhere;min-height:180px}
.slide .sn{position:absolute;top:8px;right:12px;font-size:11px;color:#9aa0a6}
.slide .st{margin:0 0 .5em;font-size:1.5em}.slide .shape{margin-bottom:.6em}.slide p{margin:.3em 0}
.notes{margin-top:14px;padding-top:8px;border-top:1px dashed #ccc;color:#555;font-size:13px;white-space:pre-wrap}
pre.code,pre.out{margin:6px 0;padding:10px 12px;border-radius:4px;overflow:auto;font:13px/1.45 Consolas,Menlo,monospace}
pre.code{background:#f6f8fa;border:1px solid #d8dee4}pre.out{background:#fff;border-left:3px solid #9aa0a6}pre.err{color:#b42318}
"""


def _wrap(title: str, body: str, notice: str = "") -> str:
    return (f'<!doctype html><html lang="pl"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{e(title)}</title><style>{_CSS}</style></head><body>{body}{notice}</body></html>")


def error_page(message: str) -> str:
    return _wrap("Podgląd niedostępny",
                 f'<div class="paper" style="text-align:center;color:#555">{e(message)}</div>')


def render_document(path: str, name: str | None, cache_dir: str | None = None,
                    cache_key: str | None = None) -> tuple[str, str]:
    """Zwraca ('html', dokument_html) albo ('pdf', ścieżka_pdf). Rzuca PreviewError."""
    try:
        if os.path.getsize(path) > MAX_PREVIEW_BYTES:
            raise PreviewError(f"Plik jest zbyt duży do podglądu (limit {MAX_PREVIEW_BYTES // 1024 // 1024} MB).", 413)
    except OSError:
        raise PreviewError("Nie znaleziono pliku.", 404)

    ext = dx.detect_ext(path, name)
    if ext in LIBREOFFICE_ONLY or (ext in dx.LEGACY_EXTS and ext not in RENDERED_FAMILIES):
        return "pdf", convert_to_pdf(path, name, cache_dir, cache_key)
    family = RENDERED_FAMILIES.get(ext)
    if not family:
        raise PreviewError("Podgląd tego formatu nie jest obsługiwany.", 415)

    out = Out()
    try:
        try:
            if family == "docx":
                _render_docx(path, out)
            elif family == "pptx":
                _render_pptx(path, out)
            elif family == "xlsx":
                _render_xlsx(path, out)
            elif family == "odt":
                _render_odt(path, out)
            elif family == "odp":
                _render_odp(path, out)
            elif family == "ods":
                _render_ods(path, out)
            elif family == "csv":
                _render_csv(path, out, ext)
            elif family == "ipynb":
                _render_ipynb(path, out)
            elif family == "rtf":
                _render_rtf(path, out)
        except _Full:
            pass
    except PreviewError:
        raise
    except zipfile.BadZipFile:
        raise PreviewError("Plik jest uszkodzony albo zabezpieczony hasłem — nie da się go wyświetlić.")
    except (AIError, ET.ParseError) as ex:
        raise PreviewError(str(ex) if isinstance(ex, AIError) else "Nie udało się odczytać struktury pliku.")
    except Exception:
        logger.exception("Preview render failed for %s", name)
        raise PreviewError("Nie udało się wygenerować podglądu tego pliku.")

    if not out.parts:
        raise PreviewError("Plik nie zawiera treści do wyświetlenia.")
    notice = ""
    if out.truncated:
        notice = '<p class="muted">Podgląd został skrócony (zbyt duży plik) — pobierz go, aby zobaczyć całość.</p>'
    return "html", _wrap(name or "Podgląd", "".join(out.parts), notice)


# --- odpowiedzi Flask ------------------------------------------------------

_CSP = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"


def _harden(resp):
    resp.headers["Content-Security-Policy"] = _CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


def document_response(path: str, name: str | None, cache_dir: str | None = None,
                      cache_key: str | None = None):
    """Gotowa odpowiedź dla endpointu preview-doc (HTML albo PDF; błąd jako strona HTML)."""
    from flask import Response, send_file
    try:
        kind, payload = render_document(path, name, cache_dir, cache_key)
    except PreviewError as ex:
        return _harden(Response(error_page(ex.message), status=ex.status, mimetype="text/html"))
    if kind == "pdf":
        resp = send_file(payload, mimetype="application/pdf", conditional=False)
        resp.headers["Content-Disposition"] = "inline; filename=preview.pdf"
        if not cache_dir:  # plik tymczasowy — sprzątamy po wysłaniu
            resp.call_on_close(lambda p=payload: os.path.exists(p) and os.remove(p))
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp
    return _harden(Response(payload, mimetype="text/html"))


def error_response(message: str, status: int = 422):
    from flask import Response
    return _harden(Response(error_page(message), status=status, mimetype="text/html"))
