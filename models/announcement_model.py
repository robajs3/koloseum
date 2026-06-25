from models import db
from datetime import datetime


class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), nullable=False)
    content = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(20), default="info")  # info, warning, danger, success
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)  # None = bez wygaśnięcia

    author = db.relationship("User", foreign_keys=[created_by])

    @property
    def is_expired(self):
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def is_visible(self):
        return self.is_active and not self.is_expired

    def __repr__(self):
        return f"<Announcement {self.title}>"