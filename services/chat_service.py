from sqlalchemy import or_, and_
from models import db, Room, RoomMember, Subject, User, ChatMessage, DirectMessage
from services.room_service import RoomService


class ChatService:
    """Logika dla panelu czatu 'z boku' (patrz templates/base.html:
    #chat-drawer i static/js/chat_widget.js): czat grupowy podpięty pod
    przedmioty (z podziałem na pokoje) oraz wiadomości prywatne (DM)."""

    # ------------------------------------------------------------------
    # Drzewo "pokój -> przedmioty" do zakładki "Pokoje" w panelu czatu
    # ------------------------------------------------------------------
    @staticmethod
    def get_sidebar_tree(user: User) -> list[dict]:
        rooms = RoomService.get_user_rooms(user)
        rooms = sorted(rooms, key=lambda r: r.name.lower())
        tree = []
        for room in rooms:
            subjects = sorted(room.subjects, key=lambda s: s.name.lower())
            tree.append({
                "id": room.id,
                "name": room.name,
                "subjects": [
                    {"id": s.id, "name": s.name, "color": s.color}
                    for s in subjects
                ],
            })
        return tree

    # ------------------------------------------------------------------
    # Czat grupowy przedmiotu (reużywa ChatMessage, ten sam model co
    # zakładka #chat na stronie przedmiotu — wiadomości są więc spójne
    # niezależnie skąd user pisze).
    # ------------------------------------------------------------------
    @staticmethod
    def get_subject_messages(subject: Subject, after_id: int = None, limit: int = 50) -> list[ChatMessage]:
        q = ChatMessage.query.filter_by(subject_id=subject.id, is_deleted=False)
        if after_id:
            q = q.filter(ChatMessage.id > after_id)
        return q.order_by(ChatMessage.created_at.asc()).limit(limit).all()

    @staticmethod
    def send_subject_message(subject: Subject, user: User, content: str) -> ChatMessage:
        msg = ChatMessage(subject_id=subject.id, user_id=user.id, content=content)
        db.session.add(msg)
        db.session.commit()
        return msg

    # ------------------------------------------------------------------
    # Kontakty do wiadomości prywatnych — inni userzy z pokoi, do których
    # należy current_user (żeby lista nie była "całą bazą userów", tylko
    # ludźmi z którymi user faktycznie ma coś wspólnego).
    # ------------------------------------------------------------------
    @staticmethod
    def get_contacts(user: User) -> list[User]:
        room_ids = [m.room_id for m in user.room_memberships]
        if not room_ids:
            return []
        mate_ids = (
            db.session.query(RoomMember.user_id)
            .filter(RoomMember.room_id.in_(room_ids), RoomMember.user_id != user.id)
            .distinct()
        )
        ids = [row[0] for row in mate_ids]
        if not ids:
            return []
        return User.query.filter(User.id.in_(ids)).order_by(User.username.asc()).all()

    @staticmethod
    def can_message(user: User, other_id: int) -> bool:
        """User może pisać na priv tylko do kogoś, z kim dzieli przynajmniej
        jeden pokój (albo jest globalnym adminem)."""
        if user.is_global_admin:
            return User.query.get(other_id) is not None
        room_ids = {m.room_id for m in user.room_memberships}
        if not room_ids:
            return False
        return (
            db.session.query(RoomMember.id)
            .filter(RoomMember.user_id == other_id, RoomMember.room_id.in_(room_ids))
            .first()
            is not None
        )

    @staticmethod
    def get_dm_thread(user: User, other_id: int, after_id: int = None, limit: int = 50) -> list[DirectMessage]:
        q = DirectMessage.query.filter(
            or_(
                and_(DirectMessage.sender_id == user.id, DirectMessage.recipient_id == other_id),
                and_(DirectMessage.sender_id == other_id, DirectMessage.recipient_id == user.id),
            )
        )
        if after_id:
            q = q.filter(DirectMessage.id > after_id)
        return q.order_by(DirectMessage.created_at.asc()).limit(limit).all()

    @staticmethod
    def send_dm(sender: User, recipient_id: int, content: str) -> DirectMessage:
        msg = DirectMessage(sender_id=sender.id, recipient_id=recipient_id, content=content)
        db.session.add(msg)
        db.session.commit()

        recipient = User.query.get(recipient_id)
        if recipient and recipient.notifications_enabled and recipient.notify_chat_message:
            from services.notification_service import NotificationService
            from flask import current_app
            prefix = current_app.config.get("PREFIX", "/koloseum")
            NotificationService.create_notification(
                user=recipient,
                title=f"Wiadomość od {sender.username}",
                body=content[:140],
                notif_type="chat",
                link=f"{prefix}/",
            )
        return msg

    @staticmethod
    def mark_dm_read(user: User, other_id: int) -> None:
        (
            DirectMessage.query
            .filter_by(sender_id=other_id, recipient_id=user.id, is_read=False)
            .update({"is_read": True})
        )
        db.session.commit()

    @staticmethod
    def unread_dm_count(user: User, other_id: int = None) -> int:
        q = DirectMessage.query.filter_by(recipient_id=user.id, is_read=False)
        if other_id:
            q = q.filter_by(sender_id=other_id)
        return q.count()

    @staticmethod
    def unread_dm_total(user: User) -> int:
        return DirectMessage.query.filter_by(recipient_id=user.id, is_read=False).count()

    @staticmethod
    def last_message_preview(user: User, other_id: int) -> DirectMessage | None:
        return (
            DirectMessage.query
            .filter(
                or_(
                    and_(DirectMessage.sender_id == user.id, DirectMessage.recipient_id == other_id),
                    and_(DirectMessage.sender_id == other_id, DirectMessage.recipient_id == user.id),
                )
            )
            .order_by(DirectMessage.created_at.desc())
            .first()
        )
