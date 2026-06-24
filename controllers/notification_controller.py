from flask import Blueprint, jsonify, request, render_template
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
    return render_template("notifications.html", notifications=notifs)


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