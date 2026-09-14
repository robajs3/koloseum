from flask import Blueprint, jsonify, request, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from models import Notification
from services.notification_service import NotificationService

notification_bp = Blueprint("notifications", __name__)


@notification_bp.route("/notifications")
@login_required
def list_notifications():
    notifs = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    return render_template(
        "notifications.html",
        notifications=notifs,
        is_muted=NotificationService.is_muted(current_user),
    )


@notification_bp.route("/notifications/unread-count")
@login_required
def unread_count():
    count = NotificationService.get_unread_count(current_user)
    return jsonify({"count": count})


@notification_bp.route("/notifications/<int:notif_id>/read", methods=["POST"])
@login_required
def mark_read(notif_id: int):
    NotificationService.mark_read(notif_id, current_user)
    return jsonify({"ok": True})


@notification_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_read():
    NotificationService.mark_all_read(current_user)
    return jsonify({"ok": True})


@notification_bp.route("/notifications/mute", methods=["POST"])
@login_required
def mute():
    """Wycisz powiadomienia push na X minut (max 1 dzień = 1440 min).
    Powiadomienia nadal lądują w panelu — wyciszamy tylko push."""
    try:
        minutes = int(request.form.get("minutes", 60))
    except (TypeError, ValueError):
        minutes = 60
    minutes = max(1, min(minutes, 24 * 60))
    NotificationService.mute_for(current_user, minutes)
    if minutes >= 60:
        label = f"{minutes // 60} godz." if minutes % 60 == 0 else f"{minutes} min"
    else:
        label = f"{minutes} min"
    flash(f"Powiadomienia wyciszone na {label}.", "success")
    return redirect(url_for("notifications.list_notifications"))


@notification_bp.route("/notifications/unmute", methods=["POST"])
@login_required
def unmute():
    NotificationService.unmute(current_user)
    flash("Wyciszenie powiadomień wyłączone.", "success")
    return redirect(url_for("notifications.list_notifications"))