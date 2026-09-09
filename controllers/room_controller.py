from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from models import Room, RoomMember, Subject, db
from services.room_service import RoomService

room_bp = Blueprint("rooms", __name__)


def get_room_or_404(room_id: int) -> Room:
    room = Room.query.get_or_404(room_id)
    if not room.is_active:
        abort(404)
    return room


def require_room_member(room_id: int) -> RoomMember:
    member = RoomService.get_room_member(room_id, current_user.id)
    if not member and not current_user.is_global_admin:
        abort(403)
    return member


def require_room_admin(room_id: int) -> RoomMember:
    member = RoomService.get_room_member(room_id, current_user.id)
    if not member or (member.role != "admin" and not current_user.is_global_admin):
        abort(403)
    return member


@room_bp.route("/rooms")
@login_required
def list_rooms():
    rooms = RoomService.get_user_rooms(current_user)
    return render_template("rooms/list.html", rooms=rooms)


@room_bp.route("/rooms/create", methods=["GET", "POST"])
@login_required
def create_room():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        desc = request.form.get("description", "").strip()
        if not name:
            flash("Podaj nazwę pokoju.", "danger")
        else:
            room = RoomService.create_room(name, desc, current_user)
            flash(f'Pokój „{room.name}" został utworzony!', "success")
            return redirect(url_for("rooms.room_detail", room_id=room.id))
    return render_template("rooms/create.html")


@room_bp.route("/rooms/join", methods=["GET", "POST"])
@login_required
def join_room():
    code = request.args.get("code", "").strip()
    if request.method == "POST":
        code = request.form.get("code", "").strip()
    if code:
        room, err = RoomService.join_room_by_code(code, current_user)
        if err and room:
            flash(err, "info")
            return redirect(url_for("rooms.room_detail", room_id=room.id))
        elif err:
            flash(err, "danger")
        else:
            flash(f'Dołączono do pokoju „{room.name}"!', "success")
            return redirect(url_for("rooms.room_detail", room_id=room.id))
    return render_template("rooms/join.html", code=code)


@room_bp.route("/join/<code>")
@login_required
def join_by_link(code: str):
    room, err = RoomService.join_room_by_code(code, current_user)
    if err and room:
        flash(err, "info")
        return redirect(url_for("rooms.room_detail", room_id=room.id))
    elif err:
        flash(err, "danger")
        return redirect(url_for("rooms.list_rooms"))
    flash(f'Dołączono do pokoju „{room.name}"!', "success")
    return redirect(url_for("rooms.room_detail", room_id=room.id))


@room_bp.route("/rooms/<int:room_id>")
@login_required
def room_detail(room_id: int):
    room = get_room_or_404(room_id)
    member = require_room_member(room_id)
    subjects = Subject.query.filter_by(room_id=room_id).all()
    members = RoomMember.query.filter_by(room_id=room_id).all()
    return render_template(
        "rooms/detail.html",
        room=room,
        member=member,
        subjects=subjects,
        members=members,
    )


@room_bp.route("/rooms/<int:room_id>/settings", methods=["GET", "POST"])
@login_required
def room_settings(room_id: int):
    room = get_room_or_404(room_id)
    require_room_admin(room_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "update":
            room.name = request.form.get("name", room.name).strip()
            room.description = request.form.get("description", room.description).strip()
            db.session.commit()
            flash("Ustawienia pokoju zaktualizowane.", "success")
        elif action == "regenerate_code":
            new_code = RoomService.regenerate_invite_code(room)
            flash(f"Nowy kod zaproszenia: {new_code}", "success")
        elif action == "remove_member":
            user_id = int(request.form.get("user_id", 0))
            ok, err = RoomService.remove_member(room, user_id, current_user)
            flash(err or "Usunięto członka.", "success" if ok else "danger")
        elif action == "change_role":
            user_id = int(request.form.get("user_id", 0))
            new_role = request.form.get("new_role", "student")
            ok, err = RoomService.update_member_role(room, user_id, new_role, current_user)
            flash(err or "Zmieniono rolę.", "success" if ok else "danger")
    members = RoomMember.query.filter_by(room_id=room_id).all()
    return render_template("rooms/settings.html", room=room, members=members)


@room_bp.route("/rooms/<int:room_id>/subjects/create", methods=["GET", "POST"])
@login_required
def create_subject(room_id: int):
    room = get_room_or_404(room_id)
    require_room_admin(room_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        desc = request.form.get("description", "").strip()
        color = request.form.get("color", "#4f46e5")
        if not name:
            flash("Podaj nazwę przedmiotu.", "danger")
        else:
            subject = RoomService.create_subject(room, name, desc, color, current_user)
            flash(f'Przedmiot „{subject.name}" dodany!', "success")
            return redirect(url_for("rooms.room_detail", room_id=room_id))
    return render_template("rooms/create_subject.html", room=room)