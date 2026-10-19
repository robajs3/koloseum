from datetime import datetime
from models import db


class FolderSummary(db.Model):
    """Przegląd AI folderu FileVault przypiętego do przedmiotu (Subject.filevault_folder_url).
    Osobna tabela, więc db.create_all() utworzy ją sam — bez ręcznych migracji.

    source_url zapamiętuje, dla jakiego linku powstał przegląd: gdy admin zmieni folder
    przedmiotu, stary przegląd przestaje być aktualny (patrz SummaryService)."""
    __tablename__ = "folder_summaries"

    STATUS_PENDING = "pending"
    STATUS_DONE = "done"
    STATUS_ERROR = "error"

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(
        db.Integer,
        db.ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    source_url = db.Column(db.String(512), nullable=True)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status = db.Column(db.String(16), nullable=False, default=STATUS_PENDING)
    summary = db.Column(db.Text, nullable=True)
    error = db.Column(db.String(256), nullable=True)
    model = db.Column(db.String(64), nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)

    subject = db.relationship(
        "Subject",
        backref=db.backref("folder_summary", uselist=False, cascade="all, delete-orphan"),
    )
