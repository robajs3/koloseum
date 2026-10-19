import os
import unittest

from flask import Flask

from services import filevault_fetch as ff
from services.errors import AIError
from tests import fixtures as fx
from tests.mock_filevault import MockFileVault


class ParseLinkTests(unittest.TestCase):
    def test_file_variants(self):
        for link, path in [
            ("https://x.pl/filevault/s/AbC_123-x", "/filevault/s/AbC_123-x"),
            ("/filevault/s/tok/preview", "/filevault/s/tok"),
            ("https://x.pl/prefix/filevault/sf/tok/file/12", "/filevault/sf/tok/file/12"),
            ("https://x.pl/filevault/sr/tok/file/7/download", "/filevault/sr/tok/file/7"),
            ("https://x.pl/filevault/srf/tok/file/7?x=1#y", "/filevault/srf/tok/file/7"),
        ]:
            self.assertEqual(ff.parse_link(link), ("file", path), link)

    def test_folder_variants(self):
        for link, path in [
            ("https://x.pl/filevault/sf/tok", "/filevault/sf/tok"),
            ("https://x.pl/filevault/shared-folder/tok/", "/filevault/shared-folder/tok"),
            ("https://x.pl/filevault/srf/tok/55", "/filevault/srf/tok/55"),
        ]:
            self.assertEqual(ff.parse_link(link), ("folder", path), link)

    def test_rejects_garbage_and_dangerous_input(self):
        for link in [None, "", "   ", "https://x.pl/", "https://x.pl/inna/sciezka", "ftp://x/filevault/s/tok",
                     "file:///etc/passwd", "javascript:alert(1)", "/filevault/s/../../etc/passwd",
                     "/filevault/s/tok/../x", "/filevault/s/to k", "/filevault/sf/tok/file/abc", "x" * 600]:
            self.assertIsNone(ff.parse_link(link), repr(link))

    def test_filename_header(self):
        self.assertEqual(ff._filename('attachment; filename="Wykład 3.docx"'), "Wykład 3.docx")
        self.assertEqual(ff._filename("attachment; filename*=UTF-8''Wyk%C5%82ad%203.docx"), "Wykład 3.docx")
        self.assertEqual(ff._filename('attachment; filename="../../etc/passwd"'), "passwd")
        self.assertEqual(ff._filename(None), "")


class FetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fv = MockFileVault().start()
        cls.app = Flask(__name__)
        cls.app.config.update(FILEVAULT_INTERNAL_URL=cls.fv.url, AI_FETCH_MAX_MB=1, AI_FETCH_TIMEOUT=5)
        cls.docx = fx.docx([fx.LONG])
        fv = cls.fv
        fv.file("/filevault/s/DOCX", cls.docx, "Wykład 3.docx")
        fv.file("/filevault/s/NONAME", cls.docx)  # brak nazwy → rozpoznanie po zawartości
        fv.page("/filevault/s/PWD", "<form>Podaj hasło</form>")
        fv.raw("/filevault/s/REDIR", 302, {"Location": "http://evil.example/steal"})
        fv.raw("/filevault/s/DENIED", 403, {})
        fv.raw("/filevault/s/BOOM", 500, {})
        fv.raw("/filevault/s/BIGHDR", 200, {"Content-Disposition": 'attachment; filename="a.pdf"',
                                           "Content-Length": str(50 * 1024 * 1024)})
        fv.raw("/filevault/s/BIGSTREAM", 200, {"Content-Disposition": 'attachment; filename="a.txt"',
                                              "X-No-CL": "1"}, b"x" * (2 * 1024 * 1024))
        fv.page("/filevault/sf/FOLD", """
            <a href="https://koloseum.example/filevault/sf/FOLD/file/1">1</a>
            <a href='file/2'>2</a>
            <a href="/filevault/sf/FOLD/file/3/preview">3</a>
            <a href="/filevault/sf/FOLD/file/1">dubel</a>
            <a href="/filevault/sf/FOLD/file/4?x=1&amp;y=2">4</a>
            <a href="/inne/miejsce">poza</a><a href="/filevault/sf/FOLD/folder/9">podfolder</a>""")
        fv.page("/filevault/sf/EMPTY", "<p>pusto</p>")

    @classmethod
    def tearDownClass(cls):
        cls.fv.stop()

    def fetch(self, path, cookie=None):
        with self.app.app_context(), ff.fetched_file(path, cookie) as (tmp, name):
            with open(tmp, "rb") as f:
                data = f.read()
            self._last_tmp = tmp
            return tmp, name, data

    def test_download_returns_bytes_name_and_cleans_up(self):
        tmp, name, data = self.fetch("/filevault/s/DOCX")
        self.assertEqual(name, "Wykład 3.docx")
        self.assertEqual(data, self.docx)
        self.assertFalse(os.path.exists(tmp), "plik tymczasowy powinien zostać usunięty")

    def test_missing_filename_is_ok(self):
        _, name, data = self.fetch("/filevault/s/NONAME")
        self.assertEqual((name, data), ("", self.docx))

    def test_sso_cookie_is_forwarded_only_when_given(self):
        self.fv.seen.clear()
        self.fetch("/filevault/s/DOCX", cookie="abc123")
        self.fetch("/filevault/s/DOCX")
        self.assertIn("sso_session=abc123", self.fv.seen[0][1])
        self.assertIsNone(self.fv.seen[1][1])

    def test_error_mapping(self):
        for path, expected in [
            ("/filevault/s/NOPE", "Nie znaleziono"),
            ("/filevault/s/DENIED", "Brak dostępu"),
            ("/filevault/s/REDIR", "Brak dostępu"),       # przekierowania NIE są śledzone
            ("/filevault/s/PWD", "nie zwrócił pliku"),    # strona HTML zamiast pliku
            ("/filevault/s/BOOM", "HTTP 500"),
            ("/filevault/s/BIGHDR", "za duży"),
            ("/filevault/s/BIGSTREAM", "za duży"),        # limit egzekwowany też bez Content-Length
        ]:
            with self.assertRaisesRegex(AIError, expected, msg=path):
                self.fetch(path)

    def test_redirect_target_is_never_contacted(self):
        self.fv.seen.clear()
        with self.assertRaises(AIError):
            self.fetch("/filevault/s/REDIR")
        self.assertEqual([p for p, _ in self.fv.seen], ["/filevault/s/REDIR"])

    def test_oversized_download_leaves_no_temp_file(self):
        import glob, tempfile
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "kolfv_*")))
        with self.assertRaises(AIError):
            self.fetch("/filevault/s/BIGSTREAM")
        self.assertEqual(set(glob.glob(os.path.join(tempfile.gettempdir(), "kolfv_*"))), before)

    def test_unreachable_filevault(self):
        app = Flask(__name__)
        app.config.update(FILEVAULT_INTERNAL_URL="http://127.0.0.1:1", AI_FETCH_TIMEOUT=2)
        with app.app_context(), self.assertLogs("services.filevault_fetch", "ERROR"), \
                self.assertRaisesRegex(AIError, "połączyć"):
            with ff.fetched_file("/filevault/s/X"):
                pass

    def test_folder_listing(self):
        with self.app.app_context():
            files = ff.list_folder_files("/filevault/sf/FOLD")
        self.assertEqual(files, ["/filevault/sf/FOLD/file/1", "/filevault/sf/FOLD/file/2",
                                 "/filevault/sf/FOLD/file/3", "/filevault/sf/FOLD/file/4"])

    def test_empty_folder_listing(self):
        with self.app.app_context():
            self.assertEqual(ff.list_folder_files("/filevault/sf/EMPTY"), [])

    def test_host_from_link_is_ignored(self):
        """SSRF: host z linku nie ma wpływu na to, dokąd idzie żądanie — tylko ścieżka
        trafia do FILEVAULT_INTERNAL_URL."""
        kind, path = ff.parse_link("http://169.254.169.254:80/filevault/s/DOCX")
        self.fv.seen.clear()
        self.fetch(path)
        self.assertEqual([p for p, _ in self.fv.seen], ["/filevault/s/DOCX"])


if __name__ == "__main__":
    unittest.main()
