from flask import Blueprint, render_template, redirect, url_for, request, flash, session, current_app
from flask_login import login_user, logout_user, login_required, current_user
from models import User, db
from services.auth_service import AuthService
from datetime import datetime
import sso_client

auth_bp = Blueprint("auth", __name__)
PREFIX = "/koloseum"


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(
            (User.email == identifier) | (User.username == identifier)
        ).first()
        if user and user.check_password(password):
            login_user(user, remember=request.form.get("remember"))
            user.last_seen = datetime.utcnow()
            db.session.commit()
            return redirect(request.args.get("next") or url_for("dashboard.index"))
        flash("Nieprawidłowy login lub hasło.", "danger")
    return render_template("auth/login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        if not username or not email or not password:
            flash("Wypełnij wszystkie pola.", "danger")
        elif password != password2:
            flash("Hasła nie są identyczne.", "danger")
        elif len(password) < 8:
            flash("Hasło musi mieć co najmniej 8 znaków.", "danger")
        else:
            user, err = AuthService.register_user(username, email, password)
            if err:
                flash(err, "danger")
            else:
                login_user(user)
                flash("Zarejestrowano pomyślnie! Witaj w Koloseum.", "success")
                return redirect(url_for("dashboard.index"))
    return render_template("auth/register.html")


@auth_bp.route("/logout")
@login_required
def logout():
    from flask import make_response
    # Globalne wylogowanie: czyścimy ciasteczko SSO i odsyłamy na ekran
    # logowania LoginHub (nie na lokalny /koloseum/login) — inaczej
    # ciasteczko sso_session zostałoby ważne i _sso_autologin zalogowałby
    # z powrotem przy następnym wejściu na dowolną appkę SSO.
    response = make_response(redirect(sso_client.login_url(url_for("dashboard.index"))))
    sso_client.clear_sso_cookie(response)
    logout_user()
    return response