from models import db, Ticket, TicketReply, User
from models.ticket_model import TICKET_STATUSES


class TicketService:

    @staticmethod
    def create_ticket(user: User, title: str, description: str) -> Ticket:
        ticket = Ticket(user_id=user.id, title=title, description=description)
        db.session.add(ticket)
        db.session.commit()
        return ticket

    @staticmethod
    def get_visible_tickets(user: User) -> list[Ticket]:
        """Zwykły user widzi tylko swoje zgłoszenia; globalny admin — wszystkie
        (żeby mógł je obsłużyć, patrz add_reply/set_status)."""
        q = Ticket.query
        if not user.is_global_admin:
            q = q.filter_by(user_id=user.id)
        return q.order_by(Ticket.updated_at.desc()).all()

    @staticmethod
    def get_ticket_for_user(ticket_id: int, user: User) -> Ticket | None:
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return None
        if ticket.user_id != user.id and not user.is_global_admin:
            return None
        return ticket

    @staticmethod
    def add_reply(ticket: Ticket, user: User, content: str) -> TicketReply:
        reply = TicketReply(ticket_id=ticket.id, user_id=user.id, content=content)
        db.session.add(reply)
        # Zgłoszenie zamknięte, do którego odpowiada osoba zgłaszająca —
        # traktujemy to jako "wznowienie" (wraca do obsługi).
        if ticket.status == "closed" and user.id == ticket.user_id:
            ticket.status = "open"
        db.session.add(ticket)  # bump updated_at (onupdate)
        db.session.commit()
        return reply

    @staticmethod
    def set_status(ticket: Ticket, status: str, requester: User) -> tuple[bool, str | None]:
        if status not in TICKET_STATUSES:
            return False, "Nieprawidłowy status."
        if not requester.is_global_admin and requester.id != ticket.user_id:
            return False, "Brak uprawnień."
        if not requester.is_global_admin and status != "closed":
            # Zgłaszający może sam tylko zamknąć swoje zgłoszenie, nie
            # zmieniać go np. na "w toku" — to robi obsługa (admin).
            return False, "Brak uprawnień."
        ticket.status = status
        db.session.commit()
        return True, None

    @staticmethod
    def open_count_for_admin() -> int:
        return Ticket.query.filter(Ticket.status != "closed").count()
