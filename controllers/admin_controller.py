from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from models import User, Room, db
from models.announcement_model import Announcement
from datetime import datetime
from functools import wraps

admin_bp = Blueprint("admin", __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_global_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


@admin_bp.route("/")
@login_required
@admin_required
def index():
    users = User.query.order_by(User.created_at.desc()).all()
    rooms = Room.query.order_by(Room.created_at.desc()).all()
    announcements = Announcement.query.order_by(Announcement.created_at.desc()).all()
    return render_template("admin/index.html", users=users, rooms=rooms, announcements=announcements)


@admin_bp.route("/users/<int:user_id>/role", methods=["POST"])
@login_required
@admin_required
def change_user_role(user_id: int):
    user = User.query.get_or_404(user_id)
    new_role = request.form.get("role", "student")
    if new_role in ("student", "global_admin"):
        user.role = new_role
        db.session.commit()
        flash(f"Zmieniono rolę użytkownika {user.username} na {new_role}.", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/rooms/<int:room_id>/deactivate", methods=["POST"])
@login_required
@admin_required
def deactivate_room(room_id: int):
    room = Room.query.get_or_404(room_id)
    room.is_active = False
    db.session.commit()
    flash(f'Pokój „{room.name}" został dezaktywowany.', "success")
    return redirect(url_for("admin.index"))

# ── Announcements ──────────────────────────────────────────────────────────────

@admin_bp.route("/announcements/add", methods=["POST"])
@login_required
@admin_required
def add_announcement():
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    ann_type = request.form.get("type", "info")
    expires_str = request.form.get("expires_at", "").strip()

    if not title or not content:
        flash("Tytuł i treść są wymagane.", "danger")
        return redirect(url_for("admin.index"))

    if ann_type not in ("info", "warning", "danger", "success"):
        ann_type = "info"

    expires_at = None
    if expires_str:
        try:
            expires_at = datetime.strptime(expires_str, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Nieprawidłowy format daty wygaśnięcia.", "danger")
            return redirect(url_for("admin.index"))

    ann = Announcement(
        title=title,
        content=content,
        type=ann_type,
        is_active=True,
        created_by=current_user.id,
        expires_at=expires_at,
    )
    db.session.add(ann)
    db.session.commit()
    flash(f'Ogłoszenie „{title}" zostało dodane.', "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/announcements/<int:ann_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_announcement(ann_id: int):
    ann = Announcement.query.get_or_404(ann_id)
    ann.is_active = not ann.is_active
    db.session.commit()
    state = "aktywowane" if ann.is_active else "dezaktywowane"
    flash(f'Ogłoszenie „{ann.title}" zostało {state}.', "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/announcements/<int:ann_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_announcement(ann_id: int):
    ann = Announcement.query.get_or_404(ann_id)
    title = ann.title
    db.session.delete(ann)
    db.session.commit()
    flash(f'Ogłoszenie „{title}" zostało usunięte.', "success")
    return redirect(url_for("admin.index"))