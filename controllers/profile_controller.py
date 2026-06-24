from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, current_app
from flask_login import login_required, current_user
from models import db
from services.auth_service import AuthService
from services.notification_service import NotificationService

profile_bp = Blueprint("profile", __name__)


@profile_bp.route("/profile")
@login_required
def profile():
    return render_template("profile/profile.html",
                           filevault_url=current_app.config.get("FILEVAULT_BASE_URL", ""))


@profile_bp.route("/profile/change-password", methods=["POST"])
@login_required
def change_password():
    old = request.form.get("old_password", "")
    new = request.form.get("new_password", "")
    new2 = request.form.get("new_password2", "")
    if new != new2:
        flash("Nowe hasła nie są identyczne.", "danger")
    else:
        ok, err = AuthService.change_password(current_user, old, new)
        flash(err or "Hasło zmienione.", "success" if ok else "danger")
    return redirect(url_for("profile.profile"))


@profile_bp.route("/profile/link-filevault", methods=["POST"])
@login_required
def link_filevault():
    token = request.form.get("fv_token", "").strip()
    if not token:
        flash("Podaj token FileVault.", "danger")
    else:
        ok, err = AuthService.link_filevault(current_user, token)
        flash(err or "Konto FileVault zostało powiązane!", "success" if ok else "danger")
    return redirect(url_for("profile.profile"))


@profile_bp.route("/profile/theme", methods=["POST"])
@login_required
def set_theme():
    theme = request.form.get("theme", "light")
    if theme in ("light", "dark"):
        current_user.theme = theme
        db.session.commit()
    return redirect(request.referrer or url_for("dashboard.index"))


@profile_bp.route("/profile/notifications", methods=["POST"])
@login_required
def update_notifications():
    current_user.notifications_enabled = "notifications_enabled" in request.form
    current_user.notify_new_exam = "notify_new_exam" in request.form
    current_user.notify_exam_reminder = "notify_exam_reminder" in request.form
    current_user.notify_chat_message = "notify_chat_message" in request.form
    try:
        days = int(request.form.get("notify_days_before", 1))
        current_user.notify_days_before = max(1, min(days, 30))
    except ValueError:
        pass
    db.session.commit()
    flash("Ustawienia powiadomień zapisane.", "success")
    return redirect(url_for("profile.profile"))


@profile_bp.route("/profile/push-subscribe", methods=["POST"])
@login_required
def push_subscribe():
    data = request.get_json()
    if data:
        NotificationService.save_push_subscription(current_user, data)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 400


@profile_bp.route("/profile/vapid-public-key")
def vapid_public_key():
    key = current_app.config.get("VAPID_PUBLIC_KEY", "")
    return jsonify({"publicKey": key})