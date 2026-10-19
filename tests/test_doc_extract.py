import os
import tempfile
import unittest

from services import doc_extract as de
from services.ai_service import _chunk_units
from services.errors import AIError
from tests import fixtures as fx


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return path

    def ex(self, name, data, max_chars=40000, hint="same"):
        path = self.write(name, data)
        return de.extract(path, name if hint == "same" else hint, max_chars)

    def test_docx(self):
        d = self.ex("w.docx", fx.docx(["Pierwszy akapit", "Drugi akapit z ą i ż"]))
        text = "\n".join(t for _, t in d.units)
        self.assertIn("Pierwszy akapit", text)
        self.assertIn("ą i ż", text)
        self.assertEqual(d.format, "docx")

    def test_docx_ignores_deleted_tracked_text(self):
        deleted = '<w:p><w:del><w:r><w:delText>USUNIĘTE</w:delText></w:r></w:del></w:p>'
        d = self.ex("w.docx", fx.docx(["zostaje"], extra_xml=deleted))
        self.assertNotIn("USUNIĘTE", d.units[0][1])

    def test_pptx_follows_presentation_order_not_filenames(self):
        # w prezentacji slide2.xml jest PIERWSZY
        d = self.ex("p.pptx", fx.pptx([("slide2", "Pierwszy w prezentacji"), ("slide1", "Drugi w prezentacji")]))
        self.assertEqual([t for _, t in d.units], ["Pierwszy w prezentacji", "Drugi w prezentacji"])
        self.assertEqual(d.unit, "slide")

    def test_xlsx_types_and_sheets(self):
        d = self.ex("s.xlsx", fx.xlsx({"Oceny": [["Imię", "Punkty", "Zdał"], ["Ala", 42.5, True]],
                                       "Terminy": [["Kolokwium", "2026-11-05"]]}))
        self.assertEqual(len(d.units), 2)
        self.assertIn("Ala | 42.5 | TRUE", d.units[0][1])
        self.assertIn("[Arkusz: Terminy]", d.units[1][1])

    def test_xlsx_each_sheet_gets_a_share_of_the_budget(self):
        big = [[f"wiersz{i}"] for i in range(5000)]
        d = self.ex("s.xlsx", fx.xlsx({"Duży": big, "Mały": [["ważne dane"]]}), max_chars=6000)
        self.assertTrue(any("ważne dane" in t for _, t in d.units))

    def test_odt(self):
        d = self.ex("o.odt", fx.odt(["Zasada dynamiki Newtona"]))
        self.assertIn("Newtona", d.units[0][1])

    def test_text_polish_windows_encoding(self):
        d = self.ex("n.txt", "Zażółć gęślą jaźń".encode("cp1250"))
        self.assertIn("Zażółć gęślą jaźń", d.units[0][1])

    def test_html_strips_scripts(self):
        d = self.ex("p.html", "<script>var tajne=1</script><h1>Tytuł</h1><p>Treść</p>")
        t = d.units[0][1]
        self.assertIn("Tytuł", t)
        self.assertNotIn("tajne", t)

    def test_binary_masquerading_as_text(self):
        with self.assertRaises(AIError):
            self.ex("x.txt", b"abc\x00\x01\x02" * 100)

    def test_format_sniffed_from_content_when_name_is_useless(self):
        d = self.ex("noext", fx.docx(["rozpoznany po zawartości"]), hint=None)
        self.assertEqual(d.format, "docx")
        d = self.ex("dump.bin", fx.docx(["rozpoznany po zawartości"]), hint="dump.bin")
        self.assertEqual(d.format, "docx")

    def test_legacy_and_unsupported_formats_give_actionable_errors(self):
        with self.assertRaisesRegex(AIError, "DOCX"):
            self.ex("a.doc", b"\xd0\xcf\x11\xe0" + b"0" * 50)
        with self.assertRaisesRegex(AIError, "stary|Stary|zaszyfrowany"):
            self.ex("a.docx", b"\xd0\xcf\x11\xe0" + b"0" * 50)  # .docx, ale w środku OLE
        with self.assertRaisesRegex(AIError, "nie obsługują"):
            self.ex("a.png", b"\x89PNG" + b"0" * 50)

    def test_corrupt_zip(self):
        with self.assertRaises(AIError):
            self.ex("a.docx", b"PK\x03\x04 to nie jest zip")

    def test_zip_bomb_guard(self):
        old = de.MAX_PART_BYTES
        de.MAX_PART_BYTES = 1000
        try:
            with self.assertRaisesRegex(AIError, "zbyt duży"):
                self.ex("w.docx", fx.docx(["x" * 5000]))
        finally:
            de.MAX_PART_BYTES = old

    def test_xml_entities_rejected(self):
        evil = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa">]>'
                f'<w:document xmlns:w="{fx.W}"><w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>')
        data = fx._zip({"word/document.xml": evil})
        with self.assertRaisesRegex(AIError, "XML"):
            self.ex("w.docx", data)

    def test_truncation_flag_and_limit(self):
        d = self.ex("w.docx", fx.docx([fx.LONG] * 100), max_chars=2000)
        self.assertTrue(d.truncated)
        self.assertLessEqual(d.chars, 2000)
        d = self.ex("w.docx", fx.docx(["krótki"]), max_chars=2000)
        self.assertFalse(d.truncated)


class ChunkTests(unittest.TestCase):
    def test_oversized_unit_is_split(self):
        chunks = _chunk_units([(1, "x" * 15000)], 6000)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(t) <= 6000 for _, _, t in chunks))

    def test_units_are_grouped_and_ranges_kept(self):
        chunks = _chunk_units([(i, "y" * 2000) for i in range(1, 7)], 6000)
        self.assertEqual([(a, b) for a, b, _ in chunks], [(1, 2), (3, 4), (5, 6)])


if __name__ == "__main__":
    unittest.main()
