from datetime import timedelta
from flask import session, current_app
from models import User, db
import requests


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

    @staticmethod
    def _verify_filevault_token(fv_token: str) -> tuple[dict | None, str | None]:
        """Pomocnicza: odpytuje FileVault API i zwraca dane usera lub błąd."""
        base_url = current_app.config.get("FILEVAULT_BASE_URL", "")
        if not base_url:
            return None, "FileVault nie jest skonfigurowany."
        try:
            r = requests.get(
                base_url + "/api/verify",
                headers={"X-API-Token": fv_token},
                timeout=5,
            )
        except requests.RequestException:
            return None, "Nie można połączyć się z FileVault."
        if not r.ok:
            return None, "Nieprawidłowy token FileVault."
        return r.json(), None

    @staticmethod
    def link_filevault(user: User, fv_token: str) -> tuple[bool, str | None]:
        """
        Weryfikuje token FileVault i zapisuje go na koncie usera w Koloseum.
        Używane z poziomu profilu (użytkownik jest już zalogowany).
        """
        data, err = AuthService._verify_filevault_token(fv_token)
        if err:
            return False, err
        user.filevault_token = fv_token
        db.session.commit()
        return True, None

    @staticmethod
    def login_with_filevault(fv_token: str) -> tuple[User | None, str | None]:
        """
        Logowanie przez token FileVault (formularz logowania).
        Szuka konta Koloseum po emailu/username z FileVault.
        """
        data, err = AuthService._verify_filevault_token(fv_token)
        if err:
            return None, err

        fv_email = data.get("email")
        fv_username = data.get("username")

        user = User.query.filter(
            (User.email == fv_email) | (User.username == fv_username)
        ).first()

        if not user:
            return None, (
                f"Brak konta Koloseum powiązanego z tym kontem FileVault "
                f"({fv_email}). Zarejestruj się najpierw."
            )
        if not user.is_active:
            return None, "Konto jest nieaktywne."

        return user, None