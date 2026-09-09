from models import db
from datetime import datetime


class Exam(db.Model):
    __tablename__ = "exams"

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    exam_date = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(256), nullable=True)
    exam_type = db.Column(db.String(50), default="kolokwium")  # kolokwium, egzamin, kartkówka, deadline, inne
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subject = db.relationship("Subject", back_populates="exams")
    creator = db.relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<Exam {self.title} @ {self.exam_date}>"


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_deleted = db.Column(db.Boolean, default=False)

    subject = db.relationship("Subject", back_populates="messages")
    author = db.relationship("User", back_populates="messages")
    attachments = db.relationship("ChatAttachment", back_populates="message", cascade="all, delete-orphan")


class ChatAttachment(db.Model):
    __tablename__ = "chat_attachments"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("chat_messages.id"), nullable=False)
    filename = db.Column(db.String(256), nullable=False)
    original_filename = db.Column(db.String(256), nullable=False)
    file_size = db.Column(db.BigInteger, default=0)
    mime_type = db.Column(db.String(128), nullable=True)
    filevault_link = db.Column(db.String(512), nullable=True)  # optional: link to filevault
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    message = db.relationship("ChatMessage", back_populates="attachments")


class StudyMaterial(db.Model):
    __tablename__ = "study_materials"

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    filename = db.Column(db.String(256), nullable=True)  # local file
    original_filename = db.Column(db.String(256), nullable=True)
    file_size = db.Column(db.BigInteger, default=0)
    mime_type = db.Column(db.String(128), nullable=True)
    filevault_link = db.Column(db.String(512), nullable=True)  # link to filevault shared file
    is_filevault_link = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    subject = db.relationship("Subject", back_populates="materials")
    uploader = db.relationship("User", foreign_keys=[uploaded_by])


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(256), nullable=False)
    body = db.Column(db.Text, nullable=True)
    type = db.Column(db.String(50), default="info")  # info, exam, reminder, chat
    link = db.Column(db.String(512), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="notifications")