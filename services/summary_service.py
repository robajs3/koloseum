"""
summary_service.py — zarządza cyklem życia podsumowań AI materiałów.

Generowanie idzie w wątku w tle (wywołanie API trwa kilka–kilkanaście sekund,
a nie chcemy blokować workera gunicorna ani requestu uploadu). Status jest
w bazie, więc frontend może go odpytywać z dowolnego workera.
"""
import logging
import os
import threading
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError

from models import db, StudyMaterial, MaterialSummary
from services import ai_service
from services.file_service import FileService

logger = logging.getLogger(__name__)

# Lokalny model na CPU jest wolny — w obrębie jednego workera generujemy po jednym
# podsumowaniu naraz, reszta czeka w kolejce (Ollama kolejkuje też między workerami).
_gen_lock = threading.Semaphore(1)

# "pending" starsze niż to uznajemy za zawieszone (np. restart workera w trakcie)
STALE_AFTER = timedelta(minutes=60)  # długie PDF-y = wiele zapytań po kolei


def is_summarizable(material: StudyMaterial) -> bool:
    """Tylko lokalnie zapisane PDF-y (nie linki FileVault)."""
    if material.is_filevault_link or not material.filename:
        return False
    return (material.original_filename or "").lower().endswith(".pdf")


class SummaryService:

    @staticmethod
    def enabled() -> bool:
        return ai_service.is_enabled()

    @staticmethod
    def user_over_limit(user_id: int) -> bool:
        limit = current_app.config["AI_SUMMARY_DAILY_LIMIT"]
        since = datetime.utcnow() - timedelta(hours=24)
        used = MaterialSummary.query.filter(
            MaterialSummary.requested_by == user_id,
            MaterialSummary.started_at >= since,
        ).count()
        return used >= limit

    @staticmethod
    def start(material: StudyMaterial, user_id: int, force: bool = False) -> MaterialSummary | None:
        """Zleca generowanie. Zwraca rekord statusu albo None, gdy nie ma czego robić
        (AI wyłączone / nie PDF). Nie startuje drugi raz, jeśli już trwa."""
        if not SummaryService.enabled() or not is_summarizable(material):
            return None

        row = MaterialSummary.query.filter_by(material_id=material.id).first()
        now = datetime.utcnow()
        if row:
            running = row.status == MaterialSummary.STATUS_PENDING and now - row.started_at < STALE_AFTER
            if running:
                return row
            if row.status == MaterialSummary.STATUS_DONE and not force:
                return row
        else:
            row = MaterialSummary(material_id=material.id)
            db.session.add(row)

        row.status = MaterialSummary.STATUS_PENDING
        row.requested_by = user_id
        row.started_at = now
        row.finished_at = None
        row.error = None
        try:
            db.session.commit()
        except IntegrityError:  # równoległy request założył rekord pierwszy
            db.session.rollback()
            return MaterialSummary.query.filter_by(material_id=material.id).first()

        app = current_app._get_current_object()
        threading.Thread(
            target=SummaryService._worker, args=(app, material.id), daemon=True
        ).start()
        return row

    @staticmethod
    def _worker(app, material_id: int) -> None:
        with app.app_context():
            row = MaterialSummary.query.filter_by(material_id=material_id).first()
            material = db.session.get(StudyMaterial, material_id)
            if not row or not material:
                return
            try:
                path = FileService.get_material_path(material)
                if not path or not os.path.exists(path):
                    raise ai_service.AIError("Nie znaleziono pliku.")
                with _gen_lock:
                    text, model = ai_service.summarize_pdf(path, material.title)
                row.summary, row.model = text, model
                row.status = MaterialSummary.STATUS_DONE
                row.error = None
            except ai_service.AIError as e:
                row.status, row.error = MaterialSummary.STATUS_ERROR, str(e)[:250]
            except Exception:
                logger.exception("Summary failed for material %s", material_id)
                row.status = MaterialSummary.STATUS_ERROR
                row.error = "Nie udało się wygenerować podsumowania."
            row.finished_at = datetime.utcnow()
            db.session.commit()

    @staticmethod
    def to_dict(row: MaterialSummary | None) -> dict:
        if not row:
            return {"status": "none"}
        status = row.status
        if status == MaterialSummary.STATUS_PENDING and datetime.utcnow() - row.started_at >= STALE_AFTER:
            status = MaterialSummary.STATUS_ERROR
            return {"status": status, "error": "Generowanie trwało zbyt długo. Spróbuj ponownie."}
        return {"status": status, "summary": row.summary, "error": row.error}
