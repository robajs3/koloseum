from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from models import User, Room, db
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
    return render_template("admin/index.html", users=users, rooms=rooms)


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