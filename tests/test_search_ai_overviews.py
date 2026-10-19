"""Wyszukiwanie w podsumowaniach AI plików (pasek /search/api + strona /search)."""
import tempfile
import unittest
from datetime import datetime

from app import create_app
from config import Config
from models import (
    db, User, Room, RoomMember, Subject, StudyMaterial, MaterialSummary, FolderSummary,
)


class SearchAiOverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

        class Cfg(Config):
            TESTING = True
            SECRET_KEY = "test"
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{cls.tmp.name}/t.db"
            UPLOAD_FOLDER = f"{cls.tmp.name}/uploads"
            PREFIX = ""

        cls.app = create_app(Cfg)
        with cls.app.app_context():
            db.create_all()
            member = User(username="student", email="s@x.pl"); member.set_password("x")
            stranger = User(username="obcy", email="o@x.pl"); stranger.set_password("x")
            db.session.add_all([member, stranger]); db.session.flush()
            room = Room(name="Informatyka", created_by=member.id); db.session.add(room); db.session.flush()
            db.session.add(RoomMember(room_id=room.id, user_id=member.id, role="student"))
            subj = Subject(name="Analiza", room_id=room.id, created_by=member.id,
                           filevault_folder_url="https://x/sf/NOW")
            db.session.add(subj); db.session.flush()

            def mat(title, status, text):
                m = StudyMaterial(subject_id=subj.id, uploaded_by=member.id, title=title)
                db.session.add(m); db.session.flush()
                db.session.add(MaterialSummary(material_id=m.id, status=status, summary=text,
                                               finished_at=datetime.utcnow()))
            mat("Wykład 3", "done", "**Temat:** twierdzenie Bolzano-Weierstrassa i ciągi zbieżne.")
            mat("Wykład 4", "pending", "twierdzenie Bolzano w trakcie generowania")
            db.session.add(FolderSummary(subject_id=subj.id, source_url="https://x/sf/NOW",
                                         status="done", summary="Folder z notatkami o szeregach Fouriera.",
                                         finished_at=datetime.utcnow()))
            db.session.commit()
            cls.member, cls.stranger = member.id, stranger.id

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def search(self, who, q):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(who); s["_fresh"] = True
        return c.get("/search/api", query_string={"q": q}).get_json()

    def test_finds_material_by_summary_text(self):
        d = self.search(self.member, "bolzano")
        items = d["results"]["ai_overviews"]
        self.assertEqual([i["title"] for i in items], ["Wykład 3"])  # pending pominięty
        self.assertNotIn("**", items[0]["subtitle"])
        self.assertEqual(d["labels"]["ai_overviews"], "Podsumowania AI plików")

    def test_multiple_words_any_order(self):
        d = self.search(self.member, "zbieżne twierdzenie")
        self.assertEqual(len(d["results"]["ai_overviews"]), 1)

    def test_finds_folder_overview(self):
        d = self.search(self.member, "fouriera")
        self.assertIn("folder FileVault", d["results"]["ai_overviews"][0]["title"])

    def test_stale_folder_overview_hidden(self):
        with self.app.app_context():
            Subject.query.first().filevault_folder_url = "https://x/sf/OTHER"
            db.session.commit()
        try:
            self.assertNotIn("ai_overviews", self.search(self.member, "fouriera")["results"])
        finally:
            with self.app.app_context():
                Subject.query.first().filevault_folder_url = "https://x/sf/NOW"
                db.session.commit()

    def test_other_room_does_not_see_overviews(self):
        self.assertEqual(self.search(self.stranger, "bolzano")["results"], {})

    def test_wildcards_are_escaped(self):
        self.assertEqual(self.search(self.member, "%%")["results"], {})


if __name__ == "__main__":
    unittest.main()
