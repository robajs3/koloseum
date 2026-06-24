from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from models.user_model import User
from models.room_model import Room, RoomMember, Subject
from models.exam_model import Exam, ChatMessage, ChatAttachment, StudyMaterial, Notification

__all__ = [
    "db",
    "User",
    "Room",
    "RoomMember",
    "Subject",
    "Exam",
    "ChatMessage",
    "ChatAttachment",
    "StudyMaterial",
    "Notification",
]