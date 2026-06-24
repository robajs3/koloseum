from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_from_directory, current_app
from flask_login import login_required, current_user
from models import Subject, Exam, ChatMessage, ChatAttachment, StudyMaterial, RoomMember, db
from services.room_service import RoomService
from services.exam_service import ExamService
from services.file_service import FileService
from datetime import datetime
import os

subject_bp = Blueprint("subjects", __name__)


def get_subject_or_404(subject_id: int) -> Subject:
    return Subject.query.get_or_404(subject_id)


def require_subject_access(subject: Subject):
    member = RoomService.get_room_member(subject.room_id, current_user.id)
    if not member and not current_user.is_global_admin:
        abort(403)
    return member


def is_room_admin(subject: Subject) -> bool:
    member = RoomService.get_room_member(subject.room_id, current_user.id)
    return member and member.role == "admin" or current_user.is_global_admin


# Rozszerzenia obsługiwane przez podgląd inline
PREVIEWABLE_MIMETYPES = {
    # Obrazy
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".svg":  "image/svg+xml",
    # PDF
    ".pdf":  "application/pdf",
    # Tekst / kod
    ".txt":  "text/plain",
    ".md":   "text/plain",
    ".csv":  "text/plain",
    ".py":   "text/plain",
    ".js":   "text/plain",
    ".html": "text/plain",
    ".css":  "text/plain",
    ".json": "text/plain",
    ".xml":  "text/plain",
    # Video
    ".mp4":  "video/mp4",
    ".webm": "video/webm",
    ".ogg":  "video/ogg",
    # Audio
    ".mp3":  "audio/mpeg",
    ".wav":  "audio/wav",
    ".oga":  "audio/ogg",
}


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
    now = datetime.utcnow()
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
    exam_type = request.form.get("exam_type", "kolokwium")
    if not title or not date_str:
        flash("Wypełnij wymagane pola.", "danger")
        return redirect(url_for("subjects.subject_detail", subject_id=subject_id))
    try:
        exam_date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M")
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
    return redirect(url_for("subjects.subject_detail", subject_id=subject_id) + "#materials")


@subject_bp.route("/subjects/<int:subject_id>/materials/<int:material_id>/preview")
@login_required
def preview_material(subject_id: int, material_id: int):
    """Serwuje plik inline do podglądu w przeglądarce (nie jako attachment)."""
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    material = StudyMaterial.query.get_or_404(material_id)

    if material.is_filevault_link:
        abort(400)  # linki FileVault nie mają lokalnego pliku

    path = FileService.get_material_path(material)
    if not path or not os.path.exists(path):
        abort(404)

    ext = os.path.splitext(material.original_filename)[1].lower()
    mimetype = PREVIEWABLE_MIMETYPES.get(ext)
    if not mimetype:
        # Plik nie obsługuje podglądu — wyślij jako attachment
        folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "materials")
        return send_from_directory(folder, material.filename, as_attachment=True,
                                   download_name=material.original_filename)

    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "materials")
    response = send_from_directory(folder, material.filename, mimetype=mimetype)
    # inline — wyświetl w przeglądarce zamiast pobierać
    response.headers["Content-Disposition"] = (
        f"inline; filename=\"{material.original_filename}\""
    )
    return response


@subject_bp.route("/subjects/<int:subject_id>/materials/<int:material_id>/download")
@login_required
def download_material(subject_id: int, material_id: int):
    subject = get_subject_or_404(subject_id)
    require_subject_access(subject)
    material = StudyMaterial.query.get_or_404(material_id)
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