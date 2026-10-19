"""
summary_service.py — zarządza cyklem życia podsumowań AI.

Co podsumowujemy (patrz source_kind):
  • "local"  — plik wgrany do Koloseum, w dowolnym formacie z doc_extract
               (PDF, Word, PowerPoint, Excel, OpenDocument, tekst)
  • "file"   — materiał będący linkiem FileVault do pojedynczego pliku
  • "folder" — link FileVault do folderu (przegląd: opis każdego pliku + całość)
oraz folder FileVault przypięty do przedmiotu (SummaryService.start_folder).

Generowanie idzie w wątku w tle (nie blokujemy workera gunicorna ani requestu
uploadu). Status jest w bazie, więc frontend może go odpytywać z dowolnego workera.
"""
import logging
import os
import threading
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError

from models import db, StudyMaterial, MaterialSummary, FolderSummary, Subject
from services import ai_service, doc_extract, filevault_fetch
from services.errors import AIError, AIBackendError
from services.file_service import FileService

logger = logging.getLogger(__name__)

# Lokalny model na CPU jest wolny — w obrębie jednego workera generujemy po jednym
# podsumowaniu naraz, reszta czeka w kolejce (Ollama kolejkuje też między workerami).
_gen_lock = threading.Semaphore(1)

# "pending" starsze niż to uznajemy za zawieszone (np. restart workera w trakcie)
STALE_AFTER = timedelta(minutes=60)  # długie pliki / foldery = wiele zapytań po kolei


def source_kind(material: StudyMaterial) -> str | None:
    """'local' | 'file' | 'folder' albo None, gdy materiału nie da się podsumować."""
    if material.is_filevault_link:
        return filevault_fetch.link_kind(material.filevault_link)
    if material.filename and doc_extract.is_supported(material.original_filename):
        return "local"
    return None


def is_summarizable(material: StudyMaterial) -> bool:
    return source_kind(material) is not None


def _summarize_link(link: str, title: str, sso_cookie: str | None) -> tuple[str, str]:
    """Podsumowanie pliku albo folderu FileVault wskazanego linkiem."""
    parsed = filevault_fetch.parse_link(link)
    if not parsed:
        raise AIError("Ten link nie wygląda na link udostępniania z FileVault.")
    kind, path = parsed

    if kind == "file":
        with filevault_fetch.fetched_file(path, sso_cookie) as (tmp, name):
            doc = ai_service.extract_for_summary(tmp, name or None)
        with _gen_lock:  # plik tymczasowy już usunięty, model liczy na tekście w pamięci
            return ai_service.summarize_extracted(doc, title)

    return _summarize_folder(path, title, sso_cookie)


def _summarize_folder(path: str, title: str, sso_cookie: str | None) -> tuple[str, str]:
    cfg = current_app.config
    files = filevault_fetch.list_folder_files(path, sso_cookie)
    if not files:
        raise AIError("Nie znaleziono plików w folderze (pusty? FileVault nie udostępnia listy plików?).")
    selected = files[: cfg["AI_FOLDER_MAX_FILES"]]

    # 1) pobieranie + ekstrakcja (I/O) — poza blokadą modelu
    docs, skipped = [], []
    for fpath in selected:
        label = f"plik #{fpath.rsplit('/', 1)[-1]}"
        try:
            with filevault_fetch.fetched_file(fpath, sso_cookie) as (tmp, name):
                label = name or label
                doc = ai_service.extract_for_summary(tmp, name or None,
                                                     max_chars=cfg["AI_FOLDER_FILE_CHARS"])
            docs.append((label, doc))
        except AIError as e:
            skipped.append((label, str(e)))

    if not docs:
        raise AIError("Żaden plik z folderu nie nadaje się do podsumowania "
                      "(obsługiwane: PDF, Word, PowerPoint, Excel, OpenDocument, tekst).")

    # 2) model — po jednym zapytaniu na plik + jedno na całość
    with _gen_lock:
        briefs = []
        for label, doc in docs:
            try:
                briefs.append((label, ai_service.brief_document(doc, label)))
            except AIBackendError:
                raise  # Ollama nie działa — próbowanie kolejnych plików tylko by to wydłużyło
            except AIError as e:
                skipped.append((label, str(e)))
        if not briefs:
            raise AIError("Nie udało się opisać żadnego pliku z folderu.")
        return ai_service.summarize_folder(briefs, skipped, title,
                                           not_analyzed=len(files) - len(selected))


class SummaryService:

    @staticmethod
    def enabled() -> bool:
        return ai_service.is_enabled()

    @staticmethod
    def user_over_limit(user_id: int) -> bool:
        limit = current_app.config["AI_SUMMARY_DAILY_LIMIT"]
        since = datetime.utcnow() - timedelta(hours=24)
        used = sum(
            m.query.filter(m.requested_by == user_id, m.started_at >= since).count()
            for m in (MaterialSummary, FolderSummary)
        )
        return used >= limit

    # --- start -------------------------------------------------------------

    @staticmethod
    def _begin(model, filt: dict, extra: dict, user_id: int, force: bool,
               fresh: bool = True):
        """Zakłada/odnawia rekord statusu. Zwraca (rekord, czy_uruchomić_wątek).
        `fresh=False` oznacza, że gotowy wynik jest nieaktualny (np. zmieniony link)."""
        row = model.query.filter_by(**filt).first()
        now = datetime.utcnow()
        if row:
            running = row.status == model.STATUS_PENDING and now - row.started_at < STALE_AFTER
            if running:
                return row, False
            if row.status == model.STATUS_DONE and fresh and not force:
                return row, False
        else:
            row = model(**filt)
            db.session.add(row)

        row.status = model.STATUS_PENDING
        row.requested_by = user_id
        row.started_at = now
        row.finished_at = None
        row.error = None
        for k, v in extra.items():
            setattr(row, k, v)
        try:
            db.session.commit()
        except IntegrityError:  # równoległy request założył rekord pierwszy
            db.session.rollback()
            return model.query.filter_by(**filt).first(), False
        return row, True

    @staticmethod
    def _spawn(target: str, obj_id: int, sso_cookie: str | None) -> None:
        app = current_app._get_current_object()
        threading.Thread(
            target=SummaryService._worker, args=(app, target, obj_id, sso_cookie), daemon=True
        ).start()

    @staticmethod
    def start(material: StudyMaterial, user_id: int, force: bool = False,
              sso_cookie: str | None = None) -> MaterialSummary | None:
        """Zleca generowanie. Zwraca rekord statusu albo None, gdy nie ma czego robić
        (AI wyłączone / format nieobsługiwany). Nie startuje drugi raz, jeśli już trwa.
        `sso_cookie` — ciasteczko SSO użytkownika, przekazywane do FileVault (linki
        wymagające logowania); trafia tylko do FILEVAULT_INTERNAL_URL."""
        if not SummaryService.enabled() or not is_summarizable(material):
            return None
        row, run = SummaryService._begin(
            MaterialSummary, {"material_id": material.id}, {}, user_id, force)
        if run:
            SummaryService._spawn("material", material.id, sso_cookie)
        return row

    @staticmethod
    def start_folder(subject: Subject, user_id: int, force: bool = False,
                     sso_cookie: str | None = None) -> FolderSummary | None:
        """Przegląd AI folderu FileVault przypiętego do przedmiotu."""
        url = subject.filevault_folder_url
        if not SummaryService.enabled() or not filevault_fetch.link_kind(url):
            return None
        existing = FolderSummary.query.filter_by(subject_id=subject.id).first()
        fresh = not existing or existing.source_url == url  # zmieniony folder = nowy przegląd
        row, run = SummaryService._begin(
            FolderSummary, {"subject_id": subject.id}, {"source_url": url},
            user_id, force, fresh=fresh)
        if run:
            SummaryService._spawn("folder", subject.id, sso_cookie)
        return row

    # --- worker ------------------------------------------------------------

    @staticmethod
    def _generate(target: str, obj_id: int, sso_cookie: str | None):
        """Zwraca (rekord_statusu, tekst, model). Rzuca AIError."""
        if target == "material":
            row = MaterialSummary.query.filter_by(material_id=obj_id).first()
            material = db.session.get(StudyMaterial, obj_id)
            if not row or not material:
                return None, None, None
            kind = source_kind(material)
            if kind == "local":
                path = FileService.get_material_path(material)
                if not path or not os.path.exists(path):
                    raise AIError("Nie znaleziono pliku.")
                doc = ai_service.extract_for_summary(path, material.original_filename)
                with _gen_lock:
                    text, model = ai_service.summarize_extracted(doc, material.title)
            elif kind in ("file", "folder"):
                text, model = _summarize_link(material.filevault_link, material.title, sso_cookie)
            else:
                raise AIError("Tego materiału nie da się podsumować.")
            return row, text, model

        row = FolderSummary.query.filter_by(subject_id=obj_id).first()
        subject = db.session.get(Subject, obj_id)
        if not row or not subject or not subject.filevault_folder_url:
            return None, None, None
        text, model = _summarize_link(subject.filevault_folder_url,
                                      f"{subject.name} — folder FileVault", sso_cookie)
        return row, text, model

    @staticmethod
    def _worker(app, target: str, obj_id: int, sso_cookie: str | None) -> None:
        with app.app_context():
            row = None
            try:
                row, text, model = SummaryService._generate(target, obj_id, sso_cookie)
                if row is None:
                    return
                row.summary, row.model = text, model
                row.status = row.STATUS_DONE
                row.error = None
            except AIError as e:
                row = row or SummaryService._row_for(target, obj_id)
                if row is None:
                    return
                row.status, row.error = row.STATUS_ERROR, str(e)[:250]
            except Exception:
                logger.exception("Summary failed for %s %s", target, obj_id)
                row = row or SummaryService._row_for(target, obj_id)
                if row is None:
                    return
                row.status = row.STATUS_ERROR
                row.error = "Nie udało się wygenerować podsumowania."
            row.finished_at = datetime.utcnow()
            db.session.commit()

    @staticmethod
    def _row_for(target: str, obj_id: int):
        if target == "material":
            return MaterialSummary.query.filter_by(material_id=obj_id).first()
        return FolderSummary.query.filter_by(subject_id=obj_id).first()

    # --- odczyt ------------------------------------------------------------

    @staticmethod
    def to_dict(row) -> dict:
        """Działa dla MaterialSummary i FolderSummary (te same pola statusu)."""
        if not row:
            return {"status": "none"}
        status = row.status
        if status == row.STATUS_PENDING and datetime.utcnow() - row.started_at >= STALE_AFTER:
            return {"status": row.STATUS_ERROR, "error": "Generowanie trwało zbyt długo. Spróbuj ponownie."}
        return {"status": status, "summary": row.summary, "error": row.error}

    @staticmethod
    def folder_to_dict(subject: Subject) -> dict:
        """Jak to_dict, ale przegląd zrobiony dla INNEGO linku niż obecny nie jest pokazywany."""
        row = FolderSummary.query.filter_by(subject_id=subject.id).first()
        if row and row.source_url != subject.filevault_folder_url:
            return {"status": "none"}
        return SummaryService.to_dict(row)
