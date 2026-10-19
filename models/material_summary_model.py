from datetime import datetime
from models import db


class MaterialSummary(db.Model):
    """Podsumowanie AI dla materiału (PDF, Word, plik z FileVault itd.). Osobna tabela, dzięki czemu
    db.create_all() utworzy ją sam — bez ręcznych migracji istniejącej bazy."""
    __tablename__ = "material_summaries"

    STATUS_PENDING = "pending"
    STATUS_DONE = "done"
    STATUS_ERROR = "error"

    id = db.Column(db.Integer, primary_key=True)
    material_id = db.Column(
        db.Integer,
        db.ForeignKey("study_materials.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status = db.Column(db.String(16), nullable=False, default=STATUS_PENDING)
    summary = db.Column(db.Text, nullable=True)
    error = db.Column(db.String(256), nullable=True)
    model = db.Column(db.String(64), nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)

    material = db.relationship("StudyMaterial", back_populates="summary")
