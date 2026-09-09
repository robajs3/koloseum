from models import db
from datetime import datetime
import secrets
import string


def generate_invite_code():
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(8))


class Room(db.Model):
    __tablename__ = "rooms"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    invite_code = db.Column(db.String(16), unique=True, default=generate_invite_code, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    creator = db.relationship("User", foreign_keys=[created_by])
    members = db.relationship("RoomMember", back_populates="room", cascade="all, delete-orphan")
    subjects = db.relationship("Subject", back_populates="room", cascade="all, delete-orphan")

    def regenerate_invite_code(self):
        self.invite_code = generate_invite_code()

    def __repr__(self):
        return f"<Room {self.name}>"


class RoomMember(db.Model):
    __tablename__ = "room_members"

    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    role = db.Column(db.String(20), default="student")  # student, admin
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    room = db.relationship("Room", back_populates="members")
    user = db.relationship("User", back_populates="room_memberships")

    __table_args__ = (db.UniqueConstraint("room_id", "user_id", name="uq_room_member"),)


class Subject(db.Model):
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    color = db.Column(db.String(7), default="#4f46e5")  # hex color
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # FileVault folder link
    filevault_folder_url = db.Column(db.String(512), nullable=True)

    room = db.relationship("Room", back_populates="subjects")
    creator = db.relationship("User", foreign_keys=[created_by])
    exams = db.relationship("Exam", back_populates="subject", cascade="all, delete-orphan")
    messages = db.relationship("ChatMessage", back_populates="subject", cascade="all, delete-orphan")
    materials = db.relationship("StudyMaterial", back_populates="subject", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Subject {self.name}>"