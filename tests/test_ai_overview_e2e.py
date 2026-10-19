"""
Test end-to-end: prawdziwa aplikacja Flask (SQLite), atrapa FileVault i podmieniony model
(ai_service._chat) — sprawdza całą ścieżkę: upload/link → wątek → pobranie → ekstrakcja →
prompt → zapis statusu → endpointy JSON → render szablonu.
"""
import io
import tempfile
import threading
import time
import unittest
from unittest import mock

from app import create_app
from config import Config
from models import db, User, Room, RoomMember, Subject, StudyMaterial, FolderSummary
from tests import fixtures as fx
from tests.mock_filevault import MockFileVault


class FakeModel:
    """Zastępuje ai_service._chat: zapisuje wywołania, zwraca przewidywalny tekst."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.fail_with = None

    def __call__(self, system, user):
        with self.lock:
            self.calls.append((system, user))
        if self.fail_with:
            raise self.fail_with
        if "opisy plików" in user or "Zrób przegląd folderu" in user:
            return "**Zawartość folderu:** materiały z analizy.\n\n**Pliki:**\n- **a** — opis", "stop"
        if system.startswith("Dostajesz tekst jednego pliku"):
            return "Krótki opis pliku.", "stop"
        return "**Temat:** testowe podsumowanie.", "stop"


class E2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fv = MockFileVault().start()

        class Cfg(Config):
            TESTING = True
            SECRET_KEY = "test"
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{cls.tmp.name}/t.db"
            UPLOAD_FOLDER = f"{cls.tmp.name}/uploads"
            PREFIX = ""
            FILEVAULT_INTERNAL_URL = cls.fv.url
            AI_SUMMARY_ENABLED = True
            OLLAMA_URL = "http://unused"
            AI_SUMMARY_DAILY_LIMIT = 100

        cls.app = create_app(Cfg)
        with cls.app.app_context():
            db.create_all()
            admin = User(username="admin", email="a@x.pl"); admin.set_password("x")
            student = User(username="student", email="s@x.pl"); student.set_password("x")
            stranger = User(username="obcy", email="o@x.pl"); stranger.set_password("x")
            db.session.add_all([admin, student, stranger]); db.session.flush()
            room = Room(name="Informatyka", created_by=admin.id); db.session.add(room); db.session.flush()
            db.session.add_all([RoomMember(room_id=room.id, user_id=admin.id, role="admin"),
                                RoomMember(room_id=room.id, user_id=student.id, role="student")])
            subj = Subject(name="Analiza", room_id=room.id, created_by=admin.id,
                           filevault_folder_url="https://koloseum.example/filevault/sf/FOLD")
            db.session.add(subj); db.session.commit()
            cls.ids = dict(admin=admin.id, student=student.id, stranger=stranger.id, subject=subj.id)

        docx = fx.docx([fx.LONG, "Twierdzenie Bolzano-Weierstrassa"])
        cls.fv.file("/filevault/s/WORD", docx, "Wykład 3.docx")
        cls.fv.file("/filevault/s/PNG", b"\x89PNG" + b"0" * 100, "foto.png")
        cls.fv.file("/filevault/sf/FOLD/file/1", docx, "Wykład 3.docx")
        cls.fv.file("/filevault/sf/FOLD/file/2",
                    fx.xlsx({"Oceny": [["Imię", "Punkty"] for _ in range(30)]}), "Oceny.xlsx")
        cls.fv.file("/filevault/sf/FOLD/file/3", b"\x89PNG" + b"0" * 100, "zdjecie.png")
        cls.fv.file("/filevault/sf/FOLD/file/4", fx.docx(["za krótki"]), "krotki.docx")
        cls.fv.page("/filevault/sf/FOLD", "".join(
            f'<a href="/filevault/sf/FOLD/file/{i}">{i}</a>' for i in (1, 2, 3, 4)))
        cls.fv.page("/filevault/sf/EMPTY", "<p>pusto</p>")
        cls.fv.page("/filevault/sf/MANY", "".join(
            f'<a href="/filevault/sf/MANY/file/{i}">{i}</a>' for i in range(1, 13)))
        for i in range(1, 13):
            cls.fv.file(f"/filevault/sf/MANY/file/{i}", docx, f"w{i}.docx")

    @classmethod
    def tearDownClass(cls):
        cls.fv.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.model = FakeModel()
        p = mock.patch("services.ai_service._chat", self.model)
        p.start(); self.addCleanup(p.stop)
        self.fv.seen.clear()

    # --- pomocnicze ---
    def client(self, who="admin"):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(self.ids[who]); s["_fresh"] = True
        return c

    def sid(self):
        return self.ids["subject"]

    def add_link(self, c, link, title):
        r = c.post(f"/subjects/{self.sid()}/materials/add", data={"title": title, "filevault_link": link})
        self.assertEqual(r.status_code, 302)
        with self.app.app_context():
            return StudyMaterial.query.filter_by(title=title).first().id

    def upload(self, c, name, data, title):
        r = c.post(f"/subjects/{self.sid()}/materials/add", data={"title": title, "file": (io.BytesIO(data), name)},
                   content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        with self.app.app_context():
            return StudyMaterial.query.filter_by(title=title).first().id

    def wait(self, c, url, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            data = c.get(url).get_json()
            if data["status"] != "pending":
                return data
            time.sleep(0.05)
        self.fail("podsumowanie nie zakończyło się w czasie")

    def mat_url(self, mid):
        return f"/subjects/{self.sid()}/materials/{mid}/summary"

    # --- testy: pojedyncze pliki ---
    def test_uploaded_word_file_is_summarized_automatically(self):
        c = self.client()
        mid = self.upload(c, "wyklad.docx", fx.docx([fx.LONG, "Twierdzenie Bolzano-Weierstrassa"]), "Wykład lokalny")
        data = self.wait(c, self.mat_url(mid))
        self.assertEqual(data["status"], "done", data)
        self.assertIn("testowe podsumowanie", data["summary"])
        system, user = self.model.calls[-1]
        self.assertIn("Twierdzenie Bolzano-Weierstrassa", user)       # tekst z docx dotarł do modelu
        self.assertIn('format="dokument Word"', user)

    def test_uploaded_excel_and_text_files(self):
        c = self.client()
        m1 = self.upload(c, "oceny.xlsx", fx.xlsx({"Oceny": [["Imię", "Punkty"]] * 40}), "Excel lokalny")
        m2 = self.upload(c, "notatki.txt", fx.LONG.encode(), "Tekst lokalny")
        self.assertEqual(self.wait(c, self.mat_url(m1))["status"], "done")
        self.assertEqual(self.wait(c, self.mat_url(m2))["status"], "done")

    def test_unsupported_local_file_gets_no_summary(self):
        c = self.client()
        mid = self.upload(c, "foto.png", b"\x89PNG" + b"0" * 50, "Obrazek lokalny")
        self.assertEqual(c.get(self.mat_url(mid)).get_json(), {"status": "none"})
        r = c.post(self.mat_url(mid))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.model.calls, [])

    def test_filevault_file_link_is_fetched_and_summarized(self):
        c = self.client()
        cookie_c = self.app.test_client()
        cookie_c.set_cookie("sso_session", "COOKIE123")
        with cookie_c.session_transaction() as s:
            s["_user_id"] = str(self.ids["admin"]); s["_fresh"] = True
        mid = self.add_link(cookie_c, "https://koloseum.example/filevault/s/WORD", "Link do Worda")
        data = self.wait(c, self.mat_url(mid))
        self.assertEqual(data["status"], "done", data)
        self.assertIn("Bolzano-Weierstrassa", self.model.calls[-1][1])
        paths = [p for p, _ in self.fv.seen]
        self.assertEqual(paths, ["/filevault/s/WORD"])
        self.assertIn("sso_session=COOKIE123", self.fv.seen[0][1])   # SSO przekazane do FileVault

    def test_filevault_unsupported_file_reports_friendly_error(self):
        c = self.client()
        mid = self.add_link(c, "https://koloseum.example/filevault/s/PNG", "Link do PNG")
        data = self.wait(c, self.mat_url(mid))
        self.assertEqual(data["status"], "error")
        self.assertIn("nie obsługują", data["error"])
        self.assertEqual(self.model.calls, [])                      # model nie został wywołany

    def test_filevault_missing_file_error_and_retry(self):
        c = self.client()
        mid = self.add_link(c, "https://koloseum.example/filevault/s/GONE", "Link usunięty")
        self.assertIn("Nie znaleziono", self.wait(c, self.mat_url(mid))["error"])
        self.fv.file("/filevault/s/GONE", fx.docx([fx.LONG]), "wrocil.docx")      # plik się pojawia
        r = c.post(self.mat_url(mid)); self.assertEqual(r.status_code, 200)
        self.assertEqual(self.wait(c, self.mat_url(mid))["status"], "done")

    def test_ollama_down_is_reported(self):
        from services.errors import AIBackendError
        self.model.fail_with = AIBackendError("Serwer AI (Ollama) jest niedostępny.")
        c = self.client()
        mid = self.upload(c, "w.docx", fx.docx([fx.LONG]), "Gdy Ollama leży")
        data = self.wait(c, self.mat_url(mid))
        self.assertEqual((data["status"], data["error"]), ("error", "Serwer AI (Ollama) jest niedostępny."))

    def test_non_filevault_link_is_not_summarizable(self):
        c = self.client()
        mid = self.add_link(c, "https://example.com/plik.pdf", "Obcy link")
        self.assertEqual(c.post(self.mat_url(mid)).status_code, 400)

    # --- testy: foldery ---
    def test_filevault_folder_link_material_is_manual_and_summarizes_files(self):
        c = self.client()
        mid = self.add_link(c, "https://koloseum.example/filevault/sf/FOLD", "Folder jako materiał")
        self.assertEqual(c.get(self.mat_url(mid)).get_json(), {"status": "none"})   # foldery nie startują same
        self.assertEqual(self.model.calls, [])
        self.assertEqual(c.post(self.mat_url(mid)).status_code, 200)
        data = self.wait(c, self.mat_url(mid))
        self.assertEqual(data["status"], "done", data)
        self.assertIn("Zawartość folderu", data["summary"])
        briefs = [u for s, u in self.model.calls if s.startswith("Dostajesz tekst jednego pliku")]
        self.assertEqual(len(briefs), 2)                              # docx + xlsx; png i krótki docx pominięte
        final = self.model.calls[-1][1]
        self.assertIn("Wykład 3.docx", final); self.assertIn("Oceny.xlsx", final)
        self.assertIn("zdjecie.png", final); self.assertIn("krotki.docx", final)  # wspomniane jako nieodczytane

    def test_subject_folder_overview_endpoints_and_permissions(self):
        c, url = self.client(), f"/subjects/{self.sid()}/folder-summary"
        self.assertEqual(c.get(url).get_json(), {"status": "none"})
        self.assertEqual(c.post(url).status_code, 200)
        data = self.wait(c, url)
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(self.client("student").get(url).get_json()["status"], "done")  # członek widzi
        self.assertEqual(self.client("stranger").get(url).status_code, 403)             # obcy nie
        self.assertEqual(self.client("stranger").post(url).status_code, 403)

    def test_subject_folder_overview_not_overwritten_by_student_but_by_admin(self):
        url = f"/subjects/{self.sid()}/folder-summary"
        c = self.client("admin"); c.post(url); self.wait(c, url)
        n = len(self.model.calls)
        s = self.client("student"); s.post(url)
        time.sleep(0.3)
        self.assertEqual(len(self.model.calls), n, "student nie może nadpisać gotowego przeglądu")
        c.post(url); self.wait(c, url)
        time.sleep(0.2)
        self.assertGreater(len(self.model.calls), n, "admin może odświeżyć")

    def test_folder_overview_goes_stale_when_folder_changes(self):
        url = f"/subjects/{self.sid()}/folder-summary"
        c = self.client(); c.post(url); self.wait(c, url)
        with self.app.app_context():
            db.session.get(Subject, self.sid()).filevault_folder_url = "https://koloseum.example/filevault/sf/EMPTY"
            db.session.commit()
        try:
            self.assertEqual(c.get(url).get_json(), {"status": "none"})   # stary przegląd nie jest pokazywany
            c.post(url)
            self.assertIn("Nie znaleziono plików", self.wait(c, url)["error"])
        finally:
            with self.app.app_context():
                db.session.get(Subject, self.sid()).filevault_folder_url = "https://koloseum.example/filevault/sf/FOLD"
                db.session.commit()

    def test_folder_file_limit(self):
        with self.app.app_context():
            db.session.get(Subject, self.sid()).filevault_folder_url = "https://koloseum.example/filevault/sf/MANY"
            db.session.commit()
        try:
            c = self.client(); url = f"/subjects/{self.sid()}/folder-summary"
            c.post(url); data = self.wait(c, url)
            self.assertEqual(data["status"], "done", data)
            briefs = [u for s, u in self.model.calls if s.startswith("Dostajesz tekst jednego pliku")]
            self.assertEqual(len(briefs), 8)                                   # AI_FOLDER_MAX_FILES
            self.assertIn("jeszcze 4 dalszych plików", self.model.calls[-1][1])
        finally:
            with self.app.app_context():
                db.session.get(Subject, self.sid()).filevault_folder_url = "https://koloseum.example/filevault/sf/FOLD"
                db.session.commit()

    def test_folder_aborts_on_first_ollama_failure(self):
        from services.errors import AIBackendError
        self.model.fail_with = AIBackendError("Serwer AI (Ollama) jest niedostępny.")
        with self.app.app_context():
            db.session.get(Subject, self.sid()).filevault_folder_url = "https://koloseum.example/filevault/sf/MANY"
            db.session.commit()
        try:
            c = self.client(); url = f"/subjects/{self.sid()}/folder-summary"
            c.post(url); data = self.wait(c, url)
            self.assertEqual(data["status"], "error")
            self.assertEqual(len(self.model.calls), 1, "nie próbujemy kolejnych plików, gdy Ollama leży")
        finally:
            with self.app.app_context():
                db.session.get(Subject, self.sid()).filevault_folder_url = "https://koloseum.example/filevault/sf/FOLD"
                db.session.commit()

    # --- szablon ---
    def test_template_shows_buttons_only_where_summary_is_possible(self):
        c = self.client()
        ok = self.upload(c, "a.docx", fx.docx([fx.LONG]), "Szablon docx")
        no = self.upload(c, "a.png", b"\x89PNG" + b"0" * 50, "Szablon png")
        fvf = self.add_link(c, "https://koloseum.example/filevault/s/WORD", "Szablon fv plik")
        fvd = self.add_link(c, "https://koloseum.example/filevault/sf/FOLD", "Szablon fv folder")
        html = c.get(f"/subjects/{self.sid()}").get_data(as_text=True)
        for mid in (ok, fvf, fvd):
            self.assertIn(f'id="sum-btn-{mid}"', html)
            self.assertIn(f'id="sum-panel-{mid}"', html)
        self.assertNotIn(f'id="sum-btn-{no}"', html)
        self.assertIn('id="sum-btn-folder"', html)
        self.assertIn('id="sum-panel-folder"', html)
        self.assertIn("Przegląd folderu (AI)", html)
        self.assertIn("toggleSummary(p.id.replace('sum-panel-', ''))", html)
        # student też widzi przycisk przeglądu folderu, ale nie ma przycisku „regeneruj” (can-regen=0)
        shtml = self.client("student").get(f"/subjects/{self.sid()}").get_data(as_text=True)
        self.assertIn('id="sum-panel-folder"', shtml)
        self.assertRegex(shtml, r'id="sum-panel-folder"[^>]*\n[^>]*\n\s*data-can-regen="0"')

    def test_ai_disabled_hides_everything(self):
        self.app.config["AI_SUMMARY_ENABLED"] = False
        try:
            html = self.client().get(f"/subjects/{self.sid()}").get_data(as_text=True)
            self.assertNotIn("sum-btn-folder", html)
            self.assertNotIn('class="ai-summary"', html)
        finally:
            self.app.config["AI_SUMMARY_ENABLED"] = True


if __name__ == "__main__":
    unittest.main()
