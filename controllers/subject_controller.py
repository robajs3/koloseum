from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_from_directory, current_app, jsonify
from flask_login import login_required, current_user
from models import normalize_exam_type, Subject, Exam, ChatMessage, ChatAttachment, StudyMaterial, RoomMember, db
from services.room_service import RoomService
from services.exam_service import ExamService
from services.file_service import FileService
from services.summary_service import SummaryService, is_summarizable, source_kind
from services import filevault_fetch, preview_service
from services.errors import AIError, NotAccepted
from utils.timezone import to_utc, utc_now
from datetime import datetime
import os

subject_bp = Blueprint("subjects", __name__)

SSO_COOKIE_NAME = os.environ.get("SSO_COOKIE_NAME", "sso_session")


def _sso_cookie() -> str | None:
    """Ciasteczko SSO usera — przekazywane (tylko do wewnętrznego FileVault) przy
    podsumowaniach linków, które mogą wymagać zalogowania."""
    return request.cookies.get(SSO_COOKIE_NAME)


def get_subject_or_404(subject_id: int) -> Subject:
    return Subject.query.get_or_404(subject_id)


def require_subject_access(subject: Subject):
    member = RoomService.get_room_member(subject.room_id, current_user.id)
    if not member and not current_user.is_global_admin:
        abort(403)
    return member


def _folder_summary_pending(subject: Subject) -> bool:
    fs = subject.folder_summary
    return bool(fs and fs.status == "pending" and fs.source_url == subject.filevault_folder_url)


def is_room_admin(subject: Subject) -> bool:
    member = RoomService.get_room_member(subject.room_id, current_user.id)
    return member and member.role == "admin" or current_user.is_global_admin


# Rozszerzenia serwowane inline do podglądu (obrazy, PDF, wideo, audio, tekst/kod).
# Dokumenty Office itd. idą osobną ścieżką — patrz preview_material_doc.
PREVIEWABLE_MIMETYPES = {
    ext: preview_service.mimetype_for("x" + ext)  # mimetype_for oczekuje nazwy pliku, nie samego rozszerzenia
    for ext in (preview_service.IMAGE_EXTS | preview_service.PDF_EXTS | preview_service.VIDEO_EXTS
                | preview_service.AUDIO_EXTS | preview_service.TEXT_EXTS)
}


def _inline_response(folder: str, filename: str, original: str, mimetype: str):
    response = send_from_directory(folder, filename, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'inline; filename="{preview_service.e(original)}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    if mimetype == "image/svg+xml":
        # SVG otwarty bezpośrednio mógłby wykonać skrypt w originie aplikacji
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    return response


def _preview_cache_dir() -> str:
    return os.path.join(current_app.config["UPLOAD_FOLDER"], "preview_cache")


@subject_bp.route("/subjects")
@login_required
def list_subjects():
    subjects = RoomService.get_user_subjects(current_user)
    upcoming = ExamService.get_user_upcoming_exams(current_user, days=30)
    return render_template("subjects/list.html", subjects=subjects, upcoming=upcoming)


@subject_bp.route("/subjects/<int:subject_id>")
@login_required
def subject_detail(subject_id: int):
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    now = utc_now()
    upcoming_exams = (
        Exam.query.filter_by(subject_id=subject_id)
        .filter(Exam.exam_date >= now)
        .order_by(Exam.exam_date.asc())
        .all()
    )
    past_exams = (
        Exam.query.filter_by(subject_id=subject_id)
        .filter(Exam.exam_date < now)
        .order_by(Exam.exam_date.desc())
        .limit(5)
        .all()
    )
    messages = (
        ChatMessage.query.filter_by(subject_id=subject_id, is_deleted=False)
        .order_by(ChatMessage.created_at.asc())
        .limit(50)
        .all()
    )
    materials = StudyMaterial.query.filter_by(subject_id=subject_id).order_by(StudyMaterial.created_at.desc()).all()
    admin = is_room_admin(subject)
    return render_template(
        "subjects/detail.html",
        subject=subject,
        upcoming_exams=upcoming_exams,
        past_exams=past_exams,
        messages=messages,
        materials=materials,
        admin=admin,
        now=now,
        previewable_extensions=set(PREVIEWABLE_MIMETYPES.keys()),
        ai_enabled=SummaryService.enabled(),
        # id materiału → 'local' | 'file' | 'folder' (tylko te, które da się podsumować)
        summarizable={m.id: k for m in materials if (k := source_kind(m))},
        ai_folder=bool(filevault_fetch.link_kind(subject.filevault_folder_url)),
        folder_summary_pending=_folder_summary_pending(subject),
    )


@subject_bp.route("/subjects/<int:subject_id>/exams/create", methods=["POST"])
@login_required
def create_exam(subject_id: int):
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    title = request.form.get("title", "").strip()
    desc = request.form.get("description", "").strip()
    date_str = request.form.get("exam_date", "")
    location = request.form.get("location", "").strip()
    exam_type = normalize_exam_type(request.form.get("exam_type", "kolokwium"))
    if not title or not date_str:
        flash("Wypełnij wymagane pola.", "danger")
        return redirect(url_for("subjects.subject_detail", subject_id=subject_id))
    try:
        exam_date = to_utc(datetime.strptime(date_str, "%Y-%m-%dT%H:%M"))
    except ValueError:
        flash("Nieprawidłowy format daty.", "danger")
        return redirect(url_for("subjects.subject_detail", subject_id=subject_id))
    ExamService.create_exam(subject, title, desc, exam_date, location, exam_type, current_user)
    flash("Termin dodany!", "success")
    return redirect(url_for("subjects.subject_detail", subject_id=subject_id))


@subject_bp.route("/subjects/<int:subject_id>/exams/<int:exam_id>/delete", methods=["POST"])
@login_required
def delete_exam(subject_id: int, exam_id: int):
    subject = get_subject_or_404(subject_id)
    if not is_room_admin(subject):
        abort(403)
    exam = Exam.query.get_or_404(exam_id)
    ExamService.delete_exam(exam)
    flash("Termin usunięty.", "success")
    return redirect(url_for("subjects.subject_detail", subject_id=subject_id))


@subject_bp.route("/subjects/<int:subject_id>/chat", methods=["POST"])
@login_required
def send_message(subject_id: int):
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    content = request.form.get("content", "").strip()
    file = request.files.get("file")
    if not content and not (file and file.filename):
        flash("Wiadomość nie może być pusta.", "danger")
        return redirect(url_for("subjects.subject_detail", subject_id=subject_id))
    msg = ChatMessage(subject_id=subject_id, user_id=current_user.id, content=content or None)
    db.session.add(msg)
    db.session.flush()
    if file and file.filename:
        _, err = FileService.add_chat_attachment(msg, file)
        if err == "too_large":
            db.session.rollback()
            flash("Aby przesyłać pliki większe niż 100MB skorzystaj z FileVault.", "warning")
            return redirect(url_for("subjects.subject_detail", subject_id=subject_id))
    db.session.commit()
    return redirect(url_for("subjects.subject_detail", subject_id=subject_id) + "#chat")


@subject_bp.route("/subjects/<int:subject_id>/materials/add", methods=["POST"])
@login_required
def add_material(subject_id: int):
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    title = request.form.get("title", "").strip()
    desc = request.form.get("description", "").strip()
    fv_link = request.form.get("filevault_link", "").strip()
    file = request.files.get("file")
    if not title:
        flash("Podaj tytuł materiału.", "danger")
        return redirect(url_for("subjects.subject_detail", subject_id=subject_id) + "#materials")
    material, err = FileService.add_study_material(subject, current_user, title, desc, file or None, fv_link or None)
    if err == "too_large":
        flash("Aby przesyłać pliki większe niż 100MB skorzystaj z FileVault.", "warning")
    elif err:
        flash(err, "danger")
    else:
        flash("Materiał dodany!", "success")
        # Plik (PDF, Word, … albo link do pliku z FileVault) → od razu zlecamy podsumowanie AI
        # w tle; nie blokuje odpowiedzi. Foldery tylko na żądanie — to wiele zapytań do modelu.
        if (material and source_kind(material) in ("local", "file") and SummaryService.enabled()
                and not SummaryService.user_over_limit(current_user.id)):
            SummaryService.start(material, current_user.id, sso_cookie=_sso_cookie())
    return redirect(url_for("subjects.subject_detail", subject_id=subject_id) + "#materials")


@subject_bp.route("/subjects/<int:subject_id>/materials/<int:material_id>/preview")
@login_required
def preview_material(subject_id: int, material_id: int):
    """Serwuje plik inline do podglądu w przeglądarce (nie jako attachment)."""
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    material = _get_material_in_subject(subject_id, material_id)

    if material.is_filevault_link:
        abort(400)  # linki FileVault nie mają lokalnego pliku

    path = FileService.get_material_path(material)
    if not path or not os.path.exists(path):
        abort(404)

    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "materials")
    mimetype = PREVIEWABLE_MIMETYPES.get(os.path.splitext(material.original_filename or "")[1].lower())
    if not mimetype:
        # Plik nie obsługuje podglądu — wyślij jako attachment
        return send_from_directory(folder, material.filename, as_attachment=True,
                                   download_name=material.original_filename)
    return _inline_response(folder, material.filename, material.original_filename, mimetype)


@subject_bp.route("/subjects/<int:subject_id>/materials/<int:material_id>/preview-doc")
@login_required
def preview_material_doc(subject_id: int, material_id: int):
    """Podgląd dokumentów Office/ODF/CSV/… wyrenderowanych po stronie serwera (HTML lub PDF).
    Działa też dla materiałów będących linkiem do pojedynczego pliku FileVault; gdy plik
    nie jest dokumentem do renderowania, zwraca 415 (front wraca wtedy do podglądu FileVault)."""
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    material = _get_material_in_subject(subject_id, material_id)

    if material.is_filevault_link:
        parsed = filevault_fetch.parse_link(material.filevault_link)
        if not parsed or parsed[0] != "file":
            return preview_service.error_response("Podgląd dostępny tylko dla pojedynczych plików.", 415)
        try:
            with filevault_fetch.fetched_file(
                    parsed[1], _sso_cookie(),
                    accept=lambda n: not n or preview_service.can_render(n)) as (tmp, name):
                # `tmp` znika po wyjściu z bloku — HTML renderujemy w środku; PDF
                # z konwersji LibreOffice jest osobnym plikiem tymczasowym.
                return preview_service.document_response(tmp, name or material.title)
        except NotAccepted:
            return preview_service.error_response("Ten format nie ma podglądu dokumentu.", 415)
        except AIError as e:
            return preview_service.error_response(str(e), 502)

    path = FileService.get_material_path(material)
    if not path or not os.path.exists(path):
        return preview_service.error_response("Nie znaleziono pliku.", 404)
    return preview_service.document_response(
        path, material.original_filename, _preview_cache_dir(), f"m_{material.filename}")


@subject_bp.route("/subjects/<int:subject_id>/materials/<int:material_id>/download")
@login_required
def download_material(subject_id: int, material_id: int):
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    material = _get_material_in_subject(subject_id, material_id)
    path = FileService.get_material_path(material)
    if not path or not os.path.exists(path):
        abort(404)
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "materials")
    return send_from_directory(folder, material.filename, as_attachment=True,
                               download_name=material.original_filename)


@subject_bp.route("/subjects/<int:subject_id>/materials/<int:material_id>/delete", methods=["POST"])
@login_required
def delete_material(subject_id: int, material_id: int):
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    material = StudyMaterial.query.get_or_404(material_id)
    if material.uploaded_by != current_user.id and not is_room_admin(subject):
        abort(403)
    FileService.delete_material(material)
    flash("Materiał usunięty.", "success")
    return redirect(url_for("subjects.subject_detail", subject_id=subject_id) + "#materials")


@subject_bp.route("/subjects/<int:subject_id>/filevault-folder", methods=["POST"])
@login_required
def set_filevault_folder(subject_id: int):
    subject = get_subject_or_404(subject_id)
    if not is_room_admin(subject):
        abort(403)
    url = request.form.get("filevault_folder_url", "").strip()
    subject.filevault_folder_url = url or None
    db.session.commit()
    flash("Folder FileVault zaktualizowany.", "success")
    return redirect(url_for("subjects.subject_detail", subject_id=subject_id))


@subject_bp.route("/attachments/<int:attachment_id>/download")
@login_required
def download_attachment(attachment_id: int):
    att = ChatAttachment.query.get_or_404(attachment_id)
    msg = ChatMessage.query.get_or_404(att.message_id)
    subject = get_subject_or_404(msg.subject_id)
    require_subject_access(subject)
    path = FileService.get_attachment_path(att)
    if not path or not os.path.exists(path):
        abort(404)
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "chat")
    return send_from_directory(folder, att.filename, as_attachment=True,
                               download_name=att.original_filename)


def _get_attachment_for_user(attachment_id: int) -> ChatAttachment:
    att = ChatAttachment.query.get_or_404(attachment_id)
    msg = ChatMessage.query.get_or_404(att.message_id)
    require_subject_access(get_subject_or_404(msg.subject_id))
    return att


@subject_bp.route("/attachments/<int:attachment_id>/preview")
@login_required
def preview_attachment(attachment_id: int):
    """Załącznik z czatu inline (podgląd), analogicznie do preview_material."""
    att = _get_attachment_for_user(attachment_id)
    path = FileService.get_attachment_path(att)
    if not path or not os.path.exists(path):
        abort(404)
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "chat")
    mimetype = PREVIEWABLE_MIMETYPES.get(os.path.splitext(att.original_filename or "")[1].lower())
    if not mimetype:
        return send_from_directory(folder, att.filename, as_attachment=True,
                                   download_name=att.original_filename)
    return _inline_response(folder, att.filename, att.original_filename, mimetype)


@subject_bp.route("/attachments/<int:attachment_id>/preview-doc")
@login_required
def preview_attachment_doc(attachment_id: int):
    att = _get_attachment_for_user(attachment_id)
    path = FileService.get_attachment_path(att)
    if not path or not os.path.exists(path):
        return preview_service.error_response("Nie znaleziono pliku.", 404)
    return preview_service.document_response(
        path, att.original_filename, _preview_cache_dir(), f"a_{att.filename}")


# --- Podsumowania AI -------------------------------------------------------

def _get_material_in_subject(subject_id: int, material_id: int) -> StudyMaterial:
    material = StudyMaterial.query.get_or_404(material_id)
    if material.subject_id != subject_id:
        abort(404)
    return material


@subject_bp.route("/subjects/<int:subject_id>/materials/<int:material_id>/summary")
@login_required
def material_summary(subject_id: int, material_id: int):
    """Status + treść podsumowania (odpytywane z JS, dopóki status == pending)."""
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    material = _get_material_in_subject(subject_id, material_id)
    return jsonify(SummaryService.to_dict(material.summary))


@subject_bp.route("/subjects/<int:subject_id>/materials/<int:material_id>/summary", methods=["POST"])
@login_required
def generate_material_summary(subject_id: int, material_id: int):
    """Ręczne wygenerowanie / ponowienie podsumowania."""
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    material = _get_material_in_subject(subject_id, material_id)

    if not SummaryService.enabled():
        return jsonify({"status": "error", "error": "Podsumowania AI są wyłączone."}), 503
    if not is_summarizable(material):
        return jsonify({"status": "error",
                        "error": "Podsumowanie działa dla plików PDF, Word, PowerPoint, Excel, "
                                 "OpenDocument, tekstowych oraz linków FileVault."}), 400

    # Nadpisać gotowe podsumowanie może tylko autor materiału lub admin pokoju;
    # pozostali mogą wygenerować brakujące albo ponowić nieudane.
    existing = material.summary
    force = bool(existing and existing.status == "done"
                 and (material.uploaded_by == current_user.id or is_room_admin(subject)))

    if SummaryService.user_over_limit(current_user.id):
        return jsonify({"status": "error",
                        "error": "Dzienny limit podsumowań AI został wykorzystany."}), 429

    row = SummaryService.start(material, current_user.id, force=force, sso_cookie=_sso_cookie())
    return jsonify(SummaryService.to_dict(row))


@subject_bp.route("/subjects/<int:subject_id>/folder-summary")
@login_required
def folder_summary(subject_id: int):
    """Status + treść przeglądu AI folderu FileVault przypiętego do przedmiotu."""
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    return jsonify(SummaryService.folder_to_dict(subject))


@subject_bp.route("/subjects/<int:subject_id>/folder-summary", methods=["POST"])
@login_required
def generate_folder_summary(subject_id: int):
    """Ręczne wygenerowanie / ponowienie przeglądu folderu."""
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)

    if not SummaryService.enabled():
        return jsonify({"status": "error", "error": "Podsumowania AI są wyłączone."}), 503
    if not filevault_fetch.link_kind(subject.filevault_folder_url):
        return jsonify({"status": "error",
                        "error": "Ten przedmiot nie ma folderu FileVault z rozpoznawanym linkiem."}), 400

    # Folder zmienia się w czasie, więc odświeżyć gotowy przegląd może admin pokoju;
    # pozostali mogą wygenerować brakujący albo ponowić nieudany.
    existing = subject.folder_summary
    force = bool(existing and existing.status == "done"
                 and existing.source_url == subject.filevault_folder_url
                 and is_room_admin(subject))

    if SummaryService.user_over_limit(current_user.id):
        return jsonify({"status": "error",
                        "error": "Dzienny limit podsumowań AI został wykorzystany."}), 429

    row = SummaryService.start_folder(subject, current_user.id, force=force,
                                      sso_cookie=_sso_cookie())
    return jsonify(SummaryService.to_dict(row))
