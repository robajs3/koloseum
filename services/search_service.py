"""search_service.py — wyszukiwanie globalne (pasek w topbarze + strona /search).

Zasada nadrzędna: user widzi WYŁĄCZNIE to, do czego i tak ma dostęp w
aplikacji — pokoje/przedmioty ze swoich pokoi, materiały i terminy z tych
przedmiotów oraz userów, z którymi dzieli pokój (tak samo jak lista
kontaktów w czacie, patrz ChatService.get_contacts). Globalny admin widzi
wszystko (tak jak w pozostałych kontrolerach).
"""
import re
import time
from sqlalchemy import and_, or_
from flask import url_for

from models import (
    db, Room, RoomMember, Subject, User, Exam, StudyMaterial,
    ChatMessage, ChatAttachment, MaterialSummary, FolderSummary,
)
from services.chat_service import ChatService
from services import filevault_client
from utils.timezone import to_local

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}")

MIN_QUERY_LEN = 2
MAX_QUERY_LEN = 80

CATEGORY_LABELS = {
    "rooms": "Pokoje",
    "subjects": "Przedmioty",
    "exams": "Terminy",
    "materials": "Materiały",
    "ai_overviews": "Podsumowania AI plików",
    "attachments": "Załączniki z czatu",
    "messages": "Wiadomości z czatów",
    "users": "Użytkownicy",
}


def normalize_query(q: str | None) -> str:
    return " ".join((q or "").split())[:MAX_QUERY_LEN]


def _like(q: str) -> str:
    """Wzorzec LIKE z ucieczką znaków specjalnych (%, _, \\) — inaczej user
    wpisujący '%' dostałby całą bazę."""
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _contains(column, q: str):
    return column.ilike(_like(q), escape="\\")


_MD_MARKUP = re.compile(r"[*_`#>]+|^\s*[-•]\s+", re.MULTILINE)


def _plain(text: str | None) -> str:
    """Podsumowania AI są w markdownie — do podglądu w wynikach zdejmujemy znaczniki."""
    return _MD_MARKUP.sub("", text or "")


def _tokens(q: str) -> list[str]:
    return [t for t in q.split() if t]


def _contains_all(column, q: str):
    """Każde słowo zapytania musi wystąpić w kolumnie (w dowolnej kolejności) —
    podsumowanie to długi tekst, więc dopasowanie całej frazy byłoby zbyt wąskie."""
    return and_(*[_contains(column, t) for t in _tokens(q)])


def _snippet(text: str | None, q: str, width: int = 70) -> str:
    """Fragment tekstu wokół pierwszego trafienia (do podglądu wiadomości)."""
    text = " ".join((text or "").split())
    idx = text.lower().find(q.lower())
    if idx < 0:
        return text[: width * 2]
    start = max(0, idx - width // 2)
    end = min(len(text), idx + len(q) + width)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


class SearchService:

    @staticmethod
    def _accessible_room_ids(user: User) -> list[int] | None:
        """None = bez ograniczeń (globalny admin)."""
        if user.is_global_admin:
            return None
        return [m.room_id for m in user.room_memberships]

    @staticmethod
    def search(user: User, q: str, limit: int = 6) -> dict:
        """Zwraca {kategoria: [wyniki]} — tylko niepuste kategorie, w stałej
        kolejności. Każdy wynik: {title, subtitle, url, icon, (action...)}."""
        q = normalize_query(q)
        if len(q) < MIN_QUERY_LEN:
            return {}

        room_ids = SearchService._accessible_room_ids(user)
        if room_ids is not None and not room_ids:
            # User bez pokoi — jedyne, co może znaleźć, to nic (kontakty też
            # pochodzą ze wspólnych pokoi).
            return {}

        def scope(col):
            return col.in_(room_ids) if room_ids is not None else True

        results: dict[str, list] = {}

        # --- Pokoje ---------------------------------------------------
        rooms = (
            Room.query.filter(Room.is_active.is_(True), scope(Room.id))
            .filter(or_(_contains(Room.name, q), _contains(Room.description, q)))
            .order_by(Room.name.asc()).limit(limit).all()
        )
        results["rooms"] = [{
            "title": r.name,
            "subtitle": (r.description or "")[:80],
            "url": url_for("rooms.room_detail", room_id=r.id),
            "icon": "door-open",
        } for r in rooms]

        # --- Przedmioty -----------------------------------------------
        subjects = (
            Subject.query.join(Room, Room.id == Subject.room_id)
            .filter(Room.is_active.is_(True), scope(Subject.room_id))
            .filter(or_(_contains(Subject.name, q), _contains(Subject.description, q)))
            .order_by(Subject.name.asc()).limit(limit).all()
        )
        results["subjects"] = [{
            "title": s.name,
            "subtitle": s.room.name,
            "url": url_for("subjects.subject_detail", subject_id=s.id),
            "icon": "book",
            "color": s.color if s.color and _HEX_COLOR.fullmatch(s.color) else None,
        } for s in subjects]

        # --- Terminy (kolokwia, egzaminy...) ----------------------------
        exams = (
            Exam.query.join(Subject, Subject.id == Exam.subject_id)
            .join(Room, Room.id == Subject.room_id)
            .filter(Room.is_active.is_(True), scope(Subject.room_id))
            .filter(or_(_contains(Exam.title, q), _contains(Exam.location, q),
                        _contains(Exam.description, q)))
            .order_by(Exam.exam_date.desc()).limit(limit).all()
        )
        results["exams"] = [{
            "title": e.title,
            "subtitle": f"{e.subject.name} · {to_local(e.exam_date).strftime('%d.%m.%Y %H:%M')}"
                        + (f" · {e.location}" if e.location else ""),
            "url": url_for("subjects.subject_detail", subject_id=e.subject_id) + "#exams",
            "icon": "calendar-alt",
        } for e in exams]

        # --- Materiały (pliki i linki FileVault dodane do przedmiotów) --
        materials = (
            StudyMaterial.query.join(Subject, Subject.id == StudyMaterial.subject_id)
            .join(Room, Room.id == Subject.room_id)
            .filter(Room.is_active.is_(True), scope(Subject.room_id))
            .filter(or_(_contains(StudyMaterial.title, q),
                        _contains(StudyMaterial.original_filename, q),
                        _contains(StudyMaterial.description, q)))
            .order_by(StudyMaterial.created_at.desc()).limit(limit).all()
        )
        results["materials"] = [{
            "title": m.title,
            "subtitle": f"{m.subject.name}"
                        + (f" · {m.original_filename}" if m.original_filename and m.original_filename != m.title else ""),
            "url": url_for("subjects.subject_detail", subject_id=m.subject_id) + "#materials",
            "icon": "link" if m.is_filevault_link else "file",
        } for m in materials]

        # --- Podsumowania AI plików (materiały + foldery FileVault) ------
        results["ai_overviews"] = SearchService._search_ai_overviews(q, limit, scope)

        # --- Załączniki z czatów przedmiotów -----------------------------
        attachments = (
            ChatAttachment.query.join(ChatMessage, ChatMessage.id == ChatAttachment.message_id)
            .join(Subject, Subject.id == ChatMessage.subject_id)
            .join(Room, Room.id == Subject.room_id)
            .filter(Room.is_active.is_(True), ChatMessage.is_deleted.is_(False),
                    scope(Subject.room_id))
            .filter(_contains(ChatAttachment.original_filename, q))
            .order_by(ChatAttachment.created_at.desc()).limit(limit).all()
        )
        results["attachments"] = [{
            "title": a.original_filename,
            "subtitle": a.message.subject.name,
            "url": url_for("subjects.download_attachment", attachment_id=a.id),
            "icon": "paperclip",
        } for a in attachments]

        # --- Wiadomości z czatów przedmiotów -------------------------------
        messages = (
            ChatMessage.query.join(Subject, Subject.id == ChatMessage.subject_id)
            .join(Room, Room.id == Subject.room_id)
            .filter(Room.is_active.is_(True), ChatMessage.is_deleted.is_(False),
                    scope(Subject.room_id))
            .filter(_contains(ChatMessage.content, q))
            .order_by(ChatMessage.created_at.desc()).limit(limit).all()
        )
        results["messages"] = [{
            "title": _snippet(m.content, q),
            "subtitle": f"{m.author.username} · {m.subject.name}",
            "url": url_for("subjects.subject_detail", subject_id=m.subject_id) + "#chat",
            "icon": "comment",
        } for m in messages]

        # --- Użytkownicy --------------------------------------------------
        results["users"] = SearchService._search_users(user, q, limit)

        return {k: v for k, v in results.items() if v}

    @staticmethod
    def _search_ai_overviews(q: str, limit: int, scope) -> list[dict]:
        """Szuka w treści wygenerowanych podsumowań AI (tylko gotowych).
        Zakres pokojów ten sam co dla materiałów — podsumowanie jest widoczne
        dokładnie dla tych, którzy widzą sam materiał/przedmiot."""
        out: list[dict] = []

        mats = (
            MaterialSummary.query
            .join(StudyMaterial, StudyMaterial.id == MaterialSummary.material_id)
            .join(Subject, Subject.id == StudyMaterial.subject_id)
            .join(Room, Room.id == Subject.room_id)
            .filter(Room.is_active.is_(True), scope(Subject.room_id),
                    MaterialSummary.status == MaterialSummary.STATUS_DONE)
            .filter(_contains_all(MaterialSummary.summary, q))
            .order_by(MaterialSummary.finished_at.desc()).limit(limit).all()
        )
        for ms in mats:
            m = ms.material
            out.append({
                "title": m.title,
                "subtitle": f"{m.subject.name} · {_snippet(_plain(ms.summary), _tokens(q)[0])}",
                "url": url_for("subjects.subject_detail", subject_id=m.subject_id) + "#materials",
                "icon": "wand-magic-sparkles",
            })

        # Przegląd folderu przypiętego do przedmiotu — pomijamy nieaktualny
        # (zrobiony dla innego linku niż obecny, jak w SummaryService.folder_to_dict).
        folders = (
            FolderSummary.query
            .join(Subject, Subject.id == FolderSummary.subject_id)
            .join(Room, Room.id == Subject.room_id)
            .filter(Room.is_active.is_(True), scope(Subject.room_id),
                    FolderSummary.status == FolderSummary.STATUS_DONE,
                    FolderSummary.source_url == Subject.filevault_folder_url)
            .filter(_contains_all(FolderSummary.summary, q))
            .order_by(FolderSummary.finished_at.desc()).limit(limit).all()
        )
        for fs in folders:
            out.append({
                "title": f"{fs.subject.name} — folder FileVault",
                "subtitle": _snippet(_plain(fs.summary), _tokens(q)[0]),
                "url": url_for("subjects.subject_detail", subject_id=fs.subject_id) + "#sum-panel-folder",
                "icon": "folder-tree",
            })

        return out[:limit]

    @staticmethod
    def _search_users(user: User, q: str, limit: int) -> list[dict]:
        query = User.query.filter(User.id != user.id)
        if not user.is_global_admin:
            # Tylko ludzie ze wspólnych pokoi (jak lista kontaktów czatu).
            ids = [c.id for c in ChatService.get_contacts(user)]
            if not ids:
                return []
            query = query.filter(User.id.in_(ids))
        # E-mail szukamy tylko dla admina — zwykły user nie powinien móc
        # "wyciągać" adresów innych osób wyszukiwarką.
        cond = _contains(User.username, q)
        if user.is_global_admin:
            cond = or_(cond, _contains(User.email, q))
        users = query.filter(cond).order_by(User.username.asc()).limit(limit).all()
        return [{
            "title": u.username,
            "subtitle": (u.email if user.is_global_admin else "Wyślij wiadomość"),
            "url": "#",
            "icon": "user",
            "action": "dm",
            "user_id": u.id,
        } for u in users]

    # ------------------------------------------------------------------
    # FileVault (osobny endpoint — zdalne API bywa wolne, nie blokujemy
    # nim reszty wyników). Odpowiedź /browse jest cache'owana krótko,
    # żeby pisanie kolejnych liter nie odpytywało FileVault za każdym razem.
    # ------------------------------------------------------------------
    _fv_cache: dict[int, tuple[float, dict, dict]] = {}
    _FV_TTL = 60

    @staticmethod
    def search_filevault(user: User, q: str, sso_cookie: str | None, limit: int = 6):
        """Zwraca (wyniki | None, błąd | None). None+błąd gdy FileVault
        niedostępny / konto niepołączone."""
        q = normalize_query(q)
        if len(q) < MIN_QUERY_LEN:
            return {}, None
        if not sso_cookie and not user.filevault_api_token:
            return None, "not_linked"

        now = time.monotonic()
        cached = SearchService._fv_cache.get(user.id)
        if cached and now - cached[0] < SearchService._FV_TTL:
            _, browse, rooms = cached
        else:
            browse = filevault_client.browse(sso_cookie_value=sso_cookie, token=user.filevault_api_token)
            if browse is None:
                return None, "unavailable"
            rooms = filevault_client.room_shared_files(
                sso_cookie_value=sso_cookie, token=user.filevault_api_token) or {}
            if len(SearchService._fv_cache) > 500:
                SearchService._fv_cache.clear()
            SearchService._fv_cache[user.id] = (now, browse, rooms)

        ql = q.lower()
        paths = {f["id"]: f.get("full_path") or f.get("name", "")
                 for f in browse.get("all_folders", [])}

        files = [{
            "title": f["name"],
            "subtitle": " · ".join(filter(None, [f.get("size_human"), paths.get(f.get("folder_id"))])),
            "icon": "file",
            "action": "fv_file",
            "id": f["id"],
            "name": f["name"],
            "previewable": bool(f.get("is_previewable")),
            "share_url": f.get("share_url") if f.get("share_active") else None,
        } for f in browse.get("all_files", []) if ql in (f.get("name") or "").lower()][:limit]

        folders = [{
            "title": f["name"],
            "subtitle": f.get("full_path") or "",
            "icon": "folder",
            "action": "fv_folder",
            "id": f["id"],
            "name": f["name"],
            "share_url": f.get("share_url") if f.get("share_active") else None,
        } for f in browse.get("all_folders", []) if ql in (f.get("name") or "").lower()][:limit]

        room_files = []
        for room in (rooms.get("rooms") if isinstance(rooms, dict) else None) or []:
            for f in room.get("files", []):
                if ql in (f.get("name") or "").lower():
                    room_files.append({
                        "title": f["name"],
                        "subtitle": " · ".join(filter(None, [room.get("name"), f.get("folder_path")])),
                        "icon": "file",
                        "action": "fv_room_file",
                        "name": f["name"],
                        "download_url": f.get("download_url"),
                        "preview_url": f.get("preview_url") if f.get("is_previewable") else None,
                    })
        room_files = room_files[:limit]

        out = {"fv_files": files, "fv_folders": folders, "fv_room_files": room_files}
        return {k: v for k, v in out.items() if v}, None


FV_CATEGORY_LABELS = {
    "fv_files": "Pliki na dysku",
    "fv_folders": "Foldery na dysku",
    "fv_room_files": "Pliki w pokojach FileVault",
}
