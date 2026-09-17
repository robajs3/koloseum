from models import db
from datetime import datetime

TICKET_STATUSES = ("open", "in_progress", "closed")


class Ticket(db.Model):
    """Zgłoszenie problemu z panelu czatu (patrz zakładka 'Zgłoszenia' w
    static/js/chat_widget.js). Celowo niezależne od czatu przedmiotu/DM —
    to prosty support-ticket, nie wiadomość między konkretnymi userami."""
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="open", index=True)  # open, in_progress, closed
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    reporter = db.relationship("User", foreign_keys=[user_id])
    replies = db.relationship("TicketReply", back_populates="ticket",
                              cascade="all, delete-orphan", order_by="TicketReply.created_at")

    def __repr__(self):
        return f"<Ticket #{self.id} {self.title!r} ({self.status})>"


class TicketReply(db.Model):
    __tablename__ = "ticket_replies"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ticket = db.relationship("Ticket", back_populates="replies")
    author = db.relationship("User", foreign_keys=[user_id])
