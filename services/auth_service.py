from models import db, User
from flask import current_app
import requests


class AuthService:

    @staticmethod
    def register_user(username: str, email: str, password: str) -> tuple[User | None, str | None]:
        if User.query.filter_by(username=username).first():
            return None, "Nazwa użytkownika jest już zajęta."
        if User.query.filter_by(email=email).first():
            return None, "Email jest już zarejestrowany."
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user, None

    @staticmethod
    def login_with_filevault(fv_token: str) -> tuple[User | None, str | None]:
        """Authenticate via FileVault token — call FileVault API to get user info."""
        base = current_app.config.get("FILEVAULT_BASE_URL", "")
        try:
            resp = requests.get(
                f"{base}/api/me",
                headers={"Authorization": f"Bearer {fv_token}"},
                timeout=5,
            )
            if resp.status_code != 200:
                return None, "Nieprawidłowy token FileVault."
            data = resp.json()
            fv_user_id = data.get("id")
            fv_email = data.get("email")
            fv_username = data.get("username")
        except Exception:
            return None, "Nie można połączyć się z FileVault."

        # Find existing linked user
        user = User.query.filter_by(filevault_user_id=fv_user_id).first()
        if user:
            user.filevault_token = fv_token
            db.session.commit()
            return user, None

        # Auto-create user linked to FileVault
        existing = User.query.filter_by(email=fv_email).first()
        if existing:
            existing.filevault_user_id = fv_user_id
            existing.filevault_token = fv_token
            db.session.commit()
            return existing, None

        # Create brand new user
        username = fv_username or f"fv_{fv_user_id}"
        while User.query.filter_by(username=username).first():
            username = f"{username}_"
        new_user = User(
            username=username,
            email=fv_email,
            filevault_user_id=fv_user_id,
            filevault_token=fv_token,
        )
        db.session.add(new_user)
        db.session.commit()
        return new_user, None

    @staticmethod
    def link_filevault(user: User, fv_token: str) -> tuple[bool, str | None]:
        base = current_app.config.get("FILEVAULT_BASE_URL", "")
        try:
            resp = requests.get(
                f"{base}/api/me",
                headers={"Authorization": f"Bearer {fv_token}"},
                timeout=5,
            )
            if resp.status_code != 200:
                return False, "Nieprawidłowy token FileVault."
            data = resp.json()
            fv_user_id = data.get("id")
        except Exception:
            return False, "Nie można połączyć się z FileVault."

        existing = User.query.filter_by(filevault_user_id=fv_user_id).first()
        if existing and existing.id != user.id:
            return False, "To konto FileVault jest już powiązane z innym użytkownikiem."

        user.filevault_user_id = fv_user_id
        user.filevault_token = fv_token
        db.session.commit()
        return True, None

    @staticmethod
    def change_password(user: User, old_password: str, new_password: str) -> tuple[bool, str | None]:
        if user.password_hash and not user.check_password(old_password):
            return False, "Stare hasło jest nieprawidłowe."
        if len(new_password) < 8:
            return False, "Hasło musi mieć co najmniej 8 znaków."
        user.set_password(new_password)
        db.session.commit()
        return True, None