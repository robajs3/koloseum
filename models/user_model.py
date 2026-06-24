from models import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(128), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=True)  # nullable for filevault-only users
    role = db.Column(db.String(20), default="student")  # student, room_admin, global_admin
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    theme = db.Column(db.String(10), default="light")

    # FileVault integration
    filevault_user_id = db.Column(db.Integer, nullable=True)
    filevault_token = db.Column(db.String(512), nullable=True)

    # Notification preferences
    notifications_enabled = db.Column(db.Boolean, default=True)
    notify_new_exam = db.Column(db.Boolean, default=True)
    notify_exam_reminder = db.Column(db.Boolean, default=True)
    notify_chat_message = db.Column(db.Boolean, default=False)
    notify_days_before = db.Column(db.Integer, default=1)

    # Push subscription
    push_subscription = db.Column(db.Text, nullable=True)

    # Relationships
    room_memberships = db.relationship("RoomMember", back_populates="user", cascade="all, delete-orphan")
    messages = db.relationship("ChatMessage", back_populates="author", cascade="all, delete-orphan")
    notifications = db.relationship("Notification", back_populates="user", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def is_global_admin(self):
        return self.role == "global_admin"

    def __repr__(self):
        return f"<User {self.username}>"