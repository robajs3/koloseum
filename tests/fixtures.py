"""Minimalne pliki Office/ODF budowane samym zipfile — testy nie wymagają python-docx itd."""
import io
import zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def docx(paragraphs, extra_xml: str = "") -> bytes:
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    return _zip({"word/document.xml":
                 f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}{extra_xml}</w:body></w:document>'})


def pptx(slides_in_presentation_order: list[tuple[str, str]]) -> bytes:
    """slides: [(nazwa_pliku_slajdu, tekst)] w KOLEJNOŚCI prezentacji (nazwy plików mogą ją przeczeć)."""
    files = {}
    rels, ids = [], []
    for i, (fname, text) in enumerate(slides_in_presentation_order, start=1):
        files[f"ppt/slides/{fname}.xml"] = (
            '<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody>'
            f'<a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
        rels.append(f'<Relationship Id="rId{i}" Target="slides/{fname}.xml"/>')
        ids.append(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>')
    files["ppt/_rels/presentation.xml.rels"] = f'<Relationships xmlns="{PR}">{"".join(rels)}</Relationships>'
    files["ppt/presentation.xml"] = (
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        f'xmlns:r="{R}"><p:sldIdLst>{"".join(ids)}</p:sldIdLst></p:presentation>')
    return _zip(files)


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    """sheets: {nazwa: [[komórki...], ...]}; str → sharedStrings, bool → t=b, liczba → v."""
    shared, files, rels, sheet_tags = [], {}, [], []
    for i, (name, rows) in enumerate(sheets.items(), start=1):
        xml_rows = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, val in enumerate(row):
                ref = f"{chr(65 + c)}{r}"
                if isinstance(val, bool):
                    cells.append(f'<c r="{ref}" t="b"><v>{int(val)}</v></c>')
                elif isinstance(val, str):
                    shared.append(val)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
                else:
                    cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        files[f"xl/worksheets/sheet{i}.xml"] = (
            f'<worksheet xmlns="{S}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
        rels.append(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>')
        sheet_tags.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
    files["xl/sharedStrings.xml"] = (
        f'<sst xmlns="{S}">' + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
    files["xl/_rels/workbook.xml.rels"] = f'<Relationships xmlns="{PR}">{"".join(rels)}</Relationships>'
    files["xl/workbook.xml"] = (
        f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets>{"".join(sheet_tags)}</sheets></workbook>')
    return _zip(files)


def odt(paragraphs) -> bytes:
    body = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    return _zip({
        "mimetype": "application/vnd.oasis.opendocument.text",
        "content.xml": '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                       f"<office:body><office:text>{body}</office:text></office:body></office:document-content>"})


LONG = ("Granica ciągu liczbowego oraz twierdzenie o trzech ciągach — omówienie wykładu "
        "z analizy matematycznej wraz z przykładami zastosowań. ") * 4  # > 200 znaków
