import os
import uuid
from werkzeug.utils import secure_filename
from flask import current_app
from models import db, StudyMaterial, ChatAttachment, Subject, User

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


def allowed_file(filename: str) -> bool:
    return "." in filename


def save_upload(file, subfolder: str = "chat") -> tuple[str | None, str, int, str]:
    """Save uploaded file. Returns (saved_filename, original_filename, size, mime_type)."""
    original = secure_filename(file.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    saved_name = f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], subfolder)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, saved_name)
    file.save(path)
    size = os.path.getsize(path)
    mime = file.content_type or "application/octet-stream"
    return saved_name, original, size, mime


class FileService:

    @staticmethod
    def add_study_material(subject: Subject, uploader: User, title: str,
                            description: str, file=None,
                            filevault_link: str = None) -> tuple[StudyMaterial | None, str | None]:
        if filevault_link:
            material = StudyMaterial(
                subject_id=subject.id,
                uploaded_by=uploader.id,
                title=title,
                description=description,
                filevault_link=filevault_link,
                is_filevault_link=True,
            )
            db.session.add(material)
            db.session.commit()
            return material, None

        if file and file.filename:
            file.seek(0, 2)
            size = file.tell()
            file.seek(0)
            if size > MAX_FILE_SIZE:
                return None, "too_large"
            saved_name, original, fsize, mime = save_upload(file, subfolder="materials")
            material = StudyMaterial(
                subject_id=subject.id,
                uploaded_by=uploader.id,
                title=title,
                description=description,
                filename=saved_name,
                original_filename=original,
                file_size=fsize,
                mime_type=mime,
            )
            db.session.add(material)
            db.session.commit()
            return material, None

        return None, "Podaj plik lub link do FileVault."

    @staticmethod
    def delete_material(material: StudyMaterial) -> None:
        if material.filename:
            path = os.path.join(
                current_app.config["UPLOAD_FOLDER"], "materials", material.filename
            )
            if os.path.exists(path):
                os.remove(path)
        db.session.delete(material)
        db.session.commit()

    @staticmethod
    def add_chat_attachment(message, file) -> tuple[ChatAttachment | None, str | None]:
        file.seek(0, 2)
        size = file.tell()
        file.seek(0)
        if size > MAX_FILE_SIZE:
            return None, "too_large"
        saved_name, original, fsize, mime = save_upload(file, subfolder="chat")
        att = ChatAttachment(
            message_id=message.id,
            filename=saved_name,
            original_filename=original,
            file_size=fsize,
            mime_type=mime,
        )
        db.session.add(att)
        db.session.commit()
        return att, None

    @staticmethod
    def get_material_path(material: StudyMaterial) -> str | None:
        if material.filename:
            return os.path.join(
                current_app.config["UPLOAD_FOLDER"], "materials", material.filename
            )
        return None

    @staticmethod
    def get_attachment_path(attachment: ChatAttachment) -> str | None:
        if attachment.filename:
            return os.path.join(
                current_app.config["UPLOAD_FOLDER"], "chat", attachment.filename
            )
        return None

    @staticmethod
    def format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"