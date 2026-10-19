"""
doc_extract.py — wyciąganie tekstu z plików na potrzeby podsumowań AI.

Obsługiwane formaty:
  PDF                       (pypdf)
  DOCX / PPTX / XLSX        (OOXML — czysty zip + XML, bez dodatkowych zależności)
  ODT / ODP / ODS           (OpenDocument — j.w.)
  TXT, MD, CSV, JSON, HTML, kod źródłowy itd.

Nieobsługiwane (czytelny komunikat zamiast cichej porażki): stare formaty
binarne DOC / XLS / PPT / RTF, skany i obrazy (brak warstwy tekstowej).

Wynik to lista "jednostek" (strona PDF-a, slajd, arkusz albo ~1000-znakowa
część zwykłego tekstu), do limitu `max_chars` znaków łącznie. Jednostki są
potem grupowane we fragmenty przez ai_service._chunk_units.

Pliki pochodzą od użytkowników (albo z FileVault), więc parsery mają
zabezpieczenia: limit rozmiaru części po rozpakowaniu (zip bomb) i odrzucanie
XML-a z deklaracjami <!ENTITY> (billion laughs / XXE).
"""
import codecs
import io
import logging
import os
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

from services.errors import AIError

logger = logging.getLogger(__name__)

# Maks. rozmiar pojedynczej części zipa PO rozpakowaniu.
MAX_PART_BYTES = 40 * 1024 * 1024
FLAT_UNIT_CHARS = 1000  # rozmiar sztucznej "części" dla dokumentów bez stron (drobna, żeby fragmenty pakowały się blisko limitu)

PDF_EXTS = {".pdf"}
OOXML_EXTS = {".docx", ".pptx", ".xlsx"}
ODF_EXTS = {".odt", ".odp", ".ods"}
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".ini", ".log", ".tex", ".sql", ".html", ".htm",
    ".py", ".js", ".ts", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs",
    ".php", ".rb", ".kt", ".swift", ".sh", ".m", ".r", ".css",
}
SUPPORTED_EXTS = PDF_EXTS | OOXML_EXTS | ODF_EXTS | TEXT_EXTS

# stare formaty binarne → w jaki nowy format je zapisać
LEGACY_EXTS = {".doc": "DOCX", ".xls": "XLSX", ".ppt": "PPTX", ".rtf": "DOCX"}

# klucz jednostki → (mianownik l.poj., mianownik l.mn.) używane w etykietach fragmentów
UNIT_NOUNS = {
    "page": ("strona", "strony"),
    "slide": ("slajd", "slajdy"),
    "sheet": ("arkusz", "arkusze"),
    "part": ("część", "części"),
}
# opis formatu przekazywany modelowi (pomaga mu dobrać ton streszczenia)
FORMAT_LABELS = {
    "pdf": "PDF", "docx": "dokument Word", "odt": "dokument tekstowy",
    "pptx": "prezentacja", "odp": "prezentacja",
    "xlsx": "arkusz kalkulacyjny", "ods": "arkusz kalkulacyjny",
    "text": "plik tekstowy",
}


@dataclass
class Extracted:
    units: list = field(default_factory=list)  # [(numer, tekst), ...]
    unit: str = "part"                         # klucz do UNIT_NOUNS
    format: str = "text"                       # klucz do FORMAT_LABELS
    truncated: bool = False

    @property
    def chars(self) -> int:
        return sum(len(t) for _, t in self.units)

    @property
    def format_label(self) -> str:
        return FORMAT_LABELS.get(self.format, "dokument")


class _Collector:
    """Zbiera jednostki do limitu znaków i pamięta, czy coś obcięto."""

    def __init__(self, max_chars: int, unit: str, fmt: str):
        self.max = max_chars
        self.total = 0
        self.doc = Extracted(unit=unit, format=fmt)

    @property
    def full(self) -> bool:
        return self.total >= self.max

    def add(self, no: int, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        room = self.max - self.total
        if len(text) > room:
            text = text[:room]
            self.doc.truncated = True
        if text:
            self.doc.units.append((no, text))
            self.total += len(text)

    def add_flat(self, lines) -> None:
        """Dokumenty bez stron: linie → jednostki ~FLAT_UNIT_CHARS znaków."""
        buf, size, no = [], 0, 1
        for line in lines:
            line = (line or "").strip()
            if not line:
                continue
            if self.full:
                self.doc.truncated = True
                buf = []
                break
            buf.append(line)
            size += len(line) + 1
            if size >= FLAT_UNIT_CHARS:
                self.add(no, "\n".join(buf))
                no, buf, size = no + 1, [], 0
        if buf:
            self.add(no, "\n".join(buf))


# --- rozpoznawanie typu ----------------------------------------------------

def _ext(name: str | None) -> str:
    return os.path.splitext(name or "")[1].lower()


def is_supported(filename: str | None) -> bool:
    return _ext(filename) in SUPPORTED_EXTS


def detect_ext(path: str, name_hint: str | None = None) -> str:
    """Rozszerzenie z nazwy; gdy go brak / jest nieznane — rozpoznanie po zawartości
    (linki FileVault nie zawsze niosą sensowną nazwę pliku)."""
    ext = _ext(name_hint) or _ext(path)
    if ext in SUPPORTED_EXTS or ext in LEGACY_EXTS:
        return ext
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return ext
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                if "word/document.xml" in names:
                    return ".docx"
                if "ppt/presentation.xml" in names:
                    return ".pptx"
                if "xl/workbook.xml" in names:
                    return ".xlsx"
                if "mimetype" in names:
                    mt = zf.read("mimetype")[:100].decode("ascii", "ignore")
                    for sfx, e in (("text", ".odt"), ("presentation", ".odp"), ("spreadsheet", ".ods")):
                        if mt.endswith(sfx):
                            return e
        except Exception:
            return ext
    if head.startswith(b"\xd0\xcf\x11\xe0"):  # OLE2 — stary Word/Excel/PowerPoint
        return ".doc"
    if not ext and head and b"\x00" not in head:
        return ".txt"
    return ext


# --- pomocnicze ------------------------------------------------------------

def _read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise AIError("Plik ma nietypową strukturę — nie udało się go odczytać.")
    with zf.open(info) as f:
        data = f.read(MAX_PART_BYTES + 1)  # limit liczony na faktycznie rozpakowanych danych
    if len(data) > MAX_PART_BYTES:
        raise AIError("Plik jest zbyt duży po rozpakowaniu, żeby go przeanalizować.")
    if b"<!ENTITY" in data:
        raise AIError("Plik zawiera nieobsługiwaną strukturę XML.")
    return data


def _iterparse(data: bytes, events=("end",)):
    return ET.iterparse(io.BytesIO(data), events=events)


def _rels(zf: zipfile.ZipFile, rels_path: str, base_dir: str) -> dict:
    """Id relacji → pełna ścieżka części w zipie."""
    out = {}
    try:
        data = _read_member(zf, rels_path)
    except AIError:
        return out
    for _, el in _iterparse(data):
        if el.tag.endswith("}Relationship") or el.tag == "Relationship":
            tid, target = el.get("Id"), el.get("Target")
            if tid and target:
                if target.startswith("/"):
                    out[tid] = target.lstrip("/")
                else:
                    out[tid] = posixpath.normpath(posixpath.join(base_dir, target))
    return out


def _natural_key(name: str) -> int:
    nums = re.findall(r"\d+", name)
    return int(nums[-1]) if nums else 0


# --- PDF -------------------------------------------------------------------

def _extract_pdf(path: str, col: _Collector) -> None:
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise AIError("PDF jest zaszyfrowany hasłem.")
    pages = reader.pages
    for i, page in enumerate(pages, start=1):
        col.add(i, page.extract_text() or "")
        if col.full:
            col.doc.truncated = col.doc.truncated or i < len(pages)
            break


# --- DOCX ------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _extract_docx(path: str, col: _Collector) -> None:
    with zipfile.ZipFile(path) as zf:
        data = _read_member(zf, "word/document.xml")

    def paragraphs():
        for _, el in _iterparse(data):
            if el.tag == _W + "p":
                parts = []
                for n in el.iter():
                    if n.tag == _W + "t":
                        parts.append(n.text or "")
                    elif n.tag == _W + "tab":
                        parts.append("\t")
                    elif n.tag in (_W + "br", _W + "cr"):
                        parts.append("\n")
                yield "".join(parts)
                el.clear()

    col.add_flat(paragraphs())


# --- PPTX ------------------------------------------------------------------

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _slide_order(zf: zipfile.ZipFile) -> list[str]:
    """Kolejność slajdów z presentation.xml (nazwy plików nie muszą jej odzwierciedlać)."""
    names = zf.namelist()
    fallback = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                      key=_natural_key)
    try:
        rels = _rels(zf, "ppt/_rels/presentation.xml.rels", "ppt")
        data = _read_member(zf, "ppt/presentation.xml")
        ordered = []
        for _, el in _iterparse(data):
            if el.tag == _P + "sldId":
                target = rels.get(el.get(_R + "id"))
                if target and target in names:
                    ordered.append(target)
        return ordered or fallback
    except Exception:
        return fallback


def _extract_pptx(path: str, col: _Collector) -> None:
    with zipfile.ZipFile(path) as zf:
        slides = _slide_order(zf)
        for i, name in enumerate(slides, start=1):
            data = _read_member(zf, name)
            paras = []
            for _, el in _iterparse(data):
                if el.tag == _A + "p":
                    paras.append("".join(t.text or "" for t in el.iter(_A + "t")))
            col.add(i, "\n".join(p for p in paras if p.strip()))
            if col.full:
                col.doc.truncated = col.doc.truncated or i < len(slides)
                break


# --- XLSX ------------------------------------------------------------------

_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        data = _read_member(zf, "xl/sharedStrings.xml")
    except AIError:
        return []
    out = []
    for _, el in _iterparse(data):
        if el.tag == _S + "si":
            # <t> bezpośrednio albo w <r> (rich text); <rPh> (fonetyka) pomijamy
            parts = []
            for ch in el:
                if ch.tag == _S + "t":
                    parts.append(ch.text or "")
                elif ch.tag == _S + "r":
                    parts.extend(t.text or "" for t in ch.findall(_S + "t"))
            out.append("".join(parts))
            el.clear()
    return out


def _sheet_rows(data: bytes, shared: list[str], budget: int):
    """Wiersze arkusza jako 'a | b | c', do ~budget znaków."""
    total = 0
    for _, el in _iterparse(data):
        if el.tag != _S + "row":
            continue
        vals = []
        for c in el.findall(_S + "c"):
            t = c.get("t")
            v = c.find(_S + "v")
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
            elif t == "e":
                continue
            else:
                val = v.text
            if val.strip():
                vals.append(val.strip())
        el.clear()
        if vals:
            line = " | ".join(vals)
            total += len(line) + 1
            yield line
            if total >= budget:
                return


def _extract_xlsx(path: str, col: _Collector) -> None:
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        rels = _rels(zf, "xl/_rels/workbook.xml.rels", "xl")
        sheets = []
        for _, el in _iterparse(_read_member(zf, "xl/workbook.xml")):
            if el.tag == _S + "sheet":
                target = rels.get(el.get(_R + "id"))
                if target:
                    sheets.append((el.get("name") or f"Arkusz{len(sheets) + 1}", target))
        if not sheets:
            raise AIError("Nie znaleziono arkuszy w pliku.")
        # każdy arkusz dostaje swój kawałek budżetu, żeby pierwszy nie zjadł wszystkiego
        budget = max(2000, col.max // len(sheets))
        for i, (name, target) in enumerate(sheets, start=1):
            rows = list(_sheet_rows(_read_member(zf, target), shared, budget))
            if rows:
                col.add(i, f"[Arkusz: {name}]\n" + "\n".join(rows))
            if col.full:
                col.doc.truncated = col.doc.truncated or i < len(sheets)
                break


# --- OpenDocument ----------------------------------------------------------

_T = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_D = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
_TB = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


def _odf_text(el) -> str:
    out = [el.text or ""]
    for ch in el:
        if ch.tag == _T + "s":
            out.append(" ")
        elif ch.tag == _T + "tab":
            out.append("\t")
        elif ch.tag == _T + "line-break":
            out.append("\n")
        else:
            out.append(_odf_text(ch))
        out.append(ch.tail or "")
    return "".join(out)


def _odf_paras(el):
    for ch in el:
        if ch.tag in (_T + "p", _T + "h"):
            yield _odf_text(ch)
        else:
            yield from _odf_paras(ch)


def _extract_odf(path: str, ext: str, col: _Collector) -> None:
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(_read_member(zf, "content.xml"))
    if ext == ".odp":
        pages = list(root.iter(_D + "page"))
        for i, page in enumerate(pages, start=1):
            col.add(i, "\n".join(_odf_paras(page)))
            if col.full:
                col.doc.truncated = col.doc.truncated or i < len(pages)
                break
    elif ext == ".ods":
        tables = list(root.iter(_TB + "table"))
        for i, tbl in enumerate(tables, start=1):
            rows = []
            for row in tbl.iter(_TB + "table-row"):
                cells = [" ".join(_odf_paras(c)).strip()
                         for c in row if c.tag in (_TB + "table-cell", _TB + "covered-table-cell")]
                cells = [c for c in cells if c]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                col.add(i, f"[Arkusz: {tbl.get(_TB + 'name') or i}]\n" + "\n".join(rows))
            if col.full:
                col.doc.truncated = col.doc.truncated or i < len(tables)
                break
    else:
        col.add_flat(_odf_paras(root))


# --- tekst / HTML ----------------------------------------------------------

class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _decode_text(raw: bytes) -> str:
    try:  # incremental: ucięty w połowie znaku wielobajtowego koniec nie jest błędem
        return codecs.getincrementaldecoder("utf-8-sig")().decode(raw, final=False)
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1250")  # typowe dla starszych polskich plików z Windowsa
    except UnicodeDecodeError:
        return raw.decode("iso-8859-2", errors="replace")


def _extract_text(path: str, ext: str, col: _Collector) -> None:
    with open(path, "rb") as f:
        raw = f.read(col.max * 4 + 4)
        more = bool(f.read(1))
    if b"\x00" in raw[:4096]:
        raise AIError("To nie jest plik tekstowy.")
    text = _decode_text(raw)
    if ext in (".html", ".htm"):
        p = _HTMLText()
        p.feed(text)
        text = "".join(p.parts)
    col.add_flat(text.splitlines())
    if more:
        col.doc.truncated = True


# --- API -------------------------------------------------------------------

def extract(path: str, filename: str | None = None, max_chars: int = 40000) -> Extracted:
    """Wyciąga tekst z pliku. Rzuca AIError z komunikatem dla użytkownika."""
    ext = detect_ext(path, filename)

    if ext in LEGACY_EXTS:
        raise AIError(f"Stary format {ext.upper()} nie jest obsługiwany — zapisz plik "
                      f"jako {LEGACY_EXTS[ext]} (albo PDF) i dodaj ponownie.")
    if ext not in SUPPORTED_EXTS:
        raise AIError(f"Podsumowania AI nie obsługują plików {ext or 'bez rozszerzenia'}. "
                      "Obsługiwane: PDF, Word, PowerPoint, Excel, OpenDocument i pliki tekstowe.")

    if ext in OOXML_EXTS:
        with open(path, "rb") as f:
            if f.read(4) == b"\xd0\xcf\x11\xe0":
                raise AIError("Plik ma rozszerzenie nowego formatu, ale jest w starym "
                              "formacie binarnym albo zaszyfrowany hasłem.")

    try:
        if ext in PDF_EXTS:
            col = _Collector(max_chars, "page", "pdf")
            _extract_pdf(path, col)
        elif ext == ".docx":
            col = _Collector(max_chars, "part", "docx")
            _extract_docx(path, col)
        elif ext == ".pptx":
            col = _Collector(max_chars, "slide", "pptx")
            _extract_pptx(path, col)
        elif ext == ".xlsx":
            col = _Collector(max_chars, "sheet", "xlsx")
            _extract_xlsx(path, col)
        elif ext in ODF_EXTS:
            unit = {".odt": "part", ".odp": "slide", ".ods": "sheet"}[ext]
            col = _Collector(max_chars, unit, ext[1:])
            _extract_odf(path, ext, col)
        else:
            col = _Collector(max_chars, "part", "text")
            _extract_text(path, ext, col)
    except AIError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, KeyError):
        raise AIError(f"Nie udało się odczytać pliku {ext.upper()} (uszkodzony?).")
    except Exception:
        logger.exception("Extraction failed for %s", ext)
        raise AIError(f"Nie udało się odczytać pliku {ext.upper()} (uszkodzony lub zaszyfrowany?).")
    return col.doc
