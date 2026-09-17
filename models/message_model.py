from models import db
from datetime import datetime


class DirectMessage(db.Model):
    """Prywatna wiadomość między dwoma userami (czat 'na priv'), niezależna
    od czatu grupowego przy przedmiocie (patrz ChatMessage w exam_model.py).
    Rozmowa między parą userów jest identyfikowana przez (sender_id,
    recipient_id) w obu kierunkach — patrz ChatService.get_dm_thread."""
    __tablename__ = "direct_messages"

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    is_read = db.Column(db.Boolean, default=False)

    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])

    def __repr__(self):
        return f"<DirectMessage {self.sender_id} -> {self.recipient_id}>"
