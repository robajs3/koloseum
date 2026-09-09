from models import db, Room, RoomMember, Subject, User
from flask import current_app


class RoomService:

    @staticmethod
    def create_room(name: str, description: str, creator: User) -> Room:
        room = Room(name=name, description=description, created_by=creator.id)
        db.session.add(room)
        db.session.flush()
        member = RoomMember(room_id=room.id, user_id=creator.id, role="admin")
        db.session.add(member)
        db.session.commit()
        return room

    @staticmethod
    def join_room_by_code(code: str, user: User) -> tuple[Room | None, str | None]:
        room = Room.query.filter_by(invite_code=code.upper(), is_active=True).first()
        if not room:
            return None, "Nieprawidłowy kod zaproszenia."
        existing = RoomMember.query.filter_by(room_id=room.id, user_id=user.id).first()
        if existing:
            return room, "Już jesteś członkiem tego pokoju."
        member = RoomMember(room_id=room.id, user_id=user.id, role="student")
        db.session.add(member)
        db.session.commit()
        return room, None

    @staticmethod
    def get_user_rooms(user: User) -> list[Room]:
        memberships = RoomMember.query.filter_by(user_id=user.id).all()
        return [m.room for m in memberships if m.room.is_active]

    @staticmethod
    def get_room_member(room_id: int, user_id: int) -> RoomMember | None:
        return RoomMember.query.filter_by(room_id=room_id, user_id=user_id).first()

    @staticmethod
    def create_subject(room: Room, name: str, description: str, color: str, creator: User) -> Subject:
        subject = Subject(
            name=name,
            description=description,
            color=color,
            room_id=room.id,
            created_by=creator.id,
        )
        db.session.add(subject)
        db.session.commit()
        return subject

    @staticmethod
    def get_user_subjects(user: User) -> list[Subject]:
        """Get all subjects from all rooms where user is a member."""
        memberships = RoomMember.query.filter_by(user_id=user.id).all()
        room_ids = [m.room_id for m in memberships]
        return Subject.query.filter(Subject.room_id.in_(room_ids)).all()

    @staticmethod
    def regenerate_invite_code(room: Room) -> str:
        room.regenerate_invite_code()
        db.session.commit()
        return room.invite_code

    @staticmethod
    def remove_member(room: Room, user_id: int, requester: User) -> tuple[bool, str | None]:
        member = RoomMember.query.filter_by(room_id=room.id, user_id=user_id).first()
        if not member:
            return False, "Użytkownik nie jest członkiem pokoju."
        requester_member = RoomMember.query.filter_by(room_id=room.id, user_id=requester.id).first()
        if not requester_member or requester_member.role != "admin":
            if not requester.is_global_admin:
                return False, "Brak uprawnień."
        db.session.delete(member)
        db.session.commit()
        return True, None

    @staticmethod
    def update_member_role(room: Room, user_id: int, new_role: str, requester: User) -> tuple[bool, str | None]:
        requester_member = RoomMember.query.filter_by(room_id=room.id, user_id=requester.id).first()
        if not requester_member or requester_member.role != "admin":
            if not requester.is_global_admin:
                return False, "Brak uprawnień."
        member = RoomMember.query.filter_by(room_id=room.id, user_id=user_id).first()
        if not member:
            return False, "Użytkownik nie jest członkiem pokoju."
        member.role = new_role
        db.session.commit()
        return True, None