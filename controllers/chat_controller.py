from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user
from models import Subject, User
from services.room_service import RoomService
from services.chat_service import ChatService
from utils.timezone import to_local

chat_bp = Blueprint("chat", __name__, url_prefix="/chat")


def _subject_or_403(subject_id: int) -> Subject:
    subject = Subject.query.get_or_404(subject_id)
    member = RoomService.get_room_member(subject.room_id, current_user.id)
    if not member and not current_user.is_global_admin:
        abort(403)
    return subject


def _serialize_subject_message(msg) -> dict:
    return {
        "id": msg.id,
        "content": msg.content,
        "created_at": to_local(msg.created_at).strftime("%H:%M"),
        "author": {
            "id": msg.author.id,
            "username": msg.author.username,
        },
        "mine": msg.user_id == current_user.id,
    }


def _serialize_dm(msg) -> dict:
    return {
        "id": msg.id,
        "content": msg.content,
        "created_at": to_local(msg.created_at).strftime("%H:%M"),
        "sender_id": msg.sender_id,
        "mine": msg.sender_id == current_user.id,
    }


@chat_bp.route("/panel-data")
@login_required
def panel_data():
    """Dane startowe panelu: drzewo pokój->przedmioty oraz lista kontaktów
    (userzy ze wspólnych pokoi) z liczbą nieprzeczytanych wiadomości."""
    tree = ChatService.get_sidebar_tree(current_user)
    contacts = []
    for u in ChatService.get_contacts(current_user):
        last = ChatService.last_message_preview(current_user, u.id)
        contacts.append({
            "id": u.id,
            "username": u.username,
            "unread": ChatService.unread_dm_count(current_user, u.id),
            "last_message": last.content[:60] if last else None,
            "last_message_at": to_local(last.created_at).strftime("%d.%m %H:%M") if last else None,
        })
    contacts.sort(key=lambda c: (-c["unread"], c["username"].lower()))
    return jsonify({
        "rooms": tree,
        "contacts": contacts,
        "unread_total": ChatService.unread_dm_total(current_user),
    })


@chat_bp.route("/subject/<int:subject_id>/messages")
@login_required
def subject_messages(subject_id: int):
    subject = _subject_or_403(subject_id)
    after_id = request.args.get("after_id", type=int)
    messages = ChatService.get_subject_messages(subject, after_id=after_id)
    return jsonify({
        "subject": {"id": subject.id, "name": subject.name},
        "messages": [_serialize_subject_message(m) for m in messages],
    })


@chat_bp.route("/subject/<int:subject_id>/messages", methods=["POST"])
@login_required
def send_subject_message(subject_id: int):
    subject = _subject_or_403(subject_id)
    data = request.get_json(silent=True) or request.form
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "empty"}), 400
    if len(content) > 4000:
        return jsonify({"error": "too_long"}), 400
    msg = ChatService.send_subject_message(subject, current_user, content)
    return jsonify({"message": _serialize_subject_message(msg)})


@chat_bp.route("/dm/<int:user_id>/messages")
@login_required
def dm_messages(user_id: int):
    if user_id == current_user.id or not ChatService.can_message(current_user, user_id):
        abort(403)
    other = User.query.get_or_404(user_id)
    after_id = request.args.get("after_id", type=int)
    messages = ChatService.get_dm_thread(current_user, user_id, after_id=after_id)
    ChatService.mark_dm_read(current_user, user_id)
    return jsonify({
        "other": {"id": other.id, "username": other.username},
        "messages": [_serialize_dm(m) for m in messages],
    })


@chat_bp.route("/dm/<int:user_id>/messages", methods=["POST"])
@login_required
def send_dm(user_id: int):
    if user_id == current_user.id or not ChatService.can_message(current_user, user_id):
        abort(403)
    data = request.get_json(silent=True) or request.form
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "empty"}), 400
    if len(content) > 4000:
        return jsonify({"error": "too_long"}), 400
    msg = ChatService.send_dm(current_user, user_id, content)
    return jsonify({"message": _serialize_dm(msg)})


@chat_bp.route("/unread-count")
@login_required
def unread_count():
    return jsonify({"unread_total": ChatService.unread_dm_total(current_user)})
