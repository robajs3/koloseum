from datetime import timedelta
from flask import session
from models import User, db


class AuthService:

    @staticmethod
    def login(username: str, password: str) -> User | None:
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and user.check_password(password):
            session.permanent = True
            session.permanent_session_lifetime = timedelta(days=7)  # type: ignore[assignment]
            session["user_id"] = user.id
            return user
        return None

    @staticmethod
    def logout() -> None:
        session.clear()

    @staticmethod
    def register(username: str, email: str, password: str) -> tuple[User | None, str | None]:
        if len(password) < 8:
            return None, "Hasło musi mieć co najmniej 8 znaków."
        if User.query.filter_by(username=username).first():
            return None, "Nazwa użytkownika jest zajęta."
        if User.query.filter_by(email=email).first():
            return None, "Email jest już zarejestrowany."
        user = User(username=username, email=email)
        user.set_password(password)
        if not User.query.first():
            user.is_admin = True
        db.session.add(user)
        db.session.commit()
        session["user_id"] = user.id
        return user, None

    @staticmethod
    def register_user(username: str, email: str, password: str) -> tuple[User | None, str | None]:
        return AuthService.register(username, email, password)

    @staticmethod
    def change_password(user: User, old: str, new: str) -> tuple[bool, str | None]:
        if not user.check_password(old):
            return False, "Stare hasło jest nieprawidłowe."
        if len(new) < 8:
            return False, "Nowe hasło musi mieć co najmniej 8 znaków."
        user.set_password(new)
        db.session.commit()
        return True, None