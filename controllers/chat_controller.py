from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user
from models import Subject, User, Ticket
from services.room_service import RoomService
from services.chat_service import ChatService
from services.ticket_service import TicketService
from utils.timezone import to_local, day_label, day_key

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
        "day_key": day_key(msg.created_at),
        "day_label": day_label(msg.created_at),
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
        "day_key": day_key(msg.created_at),
        "day_label": day_label(msg.created_at),
        "sender_id": msg.sender_id,
        "mine": msg.sender_id == current_user.id,
    }


def _ticket_or_403(ticket_id: int) -> Ticket:
    ticket = TicketService.get_ticket_for_user(ticket_id, current_user)
    if not ticket:
        abort(403)
    return ticket


def _serialize_ticket(t: Ticket) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "status": t.status,
        "reporter_username": t.reporter.username,
        "created_at": to_local(t.created_at).strftime("%d.%m.%Y %H:%M"),
        "updated_at": to_local(t.updated_at).strftime("%d.%m.%Y %H:%M"),
        "reply_count": len(t.replies),
        "mine": t.user_id == current_user.id,
    }


def _serialize_ticket_entry(obj, is_first: bool = False) -> dict:
    """Ujednolica zgłoszenie (pierwszy 'wpis' wątku) i odpowiedzi w jeden
    kształt, żeby front mógł je renderować jak zwykłe dymki czatu."""
    if is_first:
        author = obj.reporter
        content = obj.description
        created_at = obj.created_at
        user_id = obj.user_id
    else:
        author = obj.author
        content = obj.content
        created_at = obj.created_at
        user_id = obj.user_id
    return {
        "id": f"t{obj.id}" if is_first else f"r{obj.id}",
        "content": content,
        "created_at": to_local(created_at).strftime("%H:%M"),
        "day_key": day_key(created_at),
        "day_label": day_label(created_at),
        "author": {"id": author.id, "username": author.username},
        "is_staff": author.is_global_admin,
        "mine": user_id == current_user.id,
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
        "open_tickets_admin": TicketService.open_count_for_admin() if current_user.is_global_admin else 0,
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
    return jsonify({
        "unread_total": ChatService.unread_dm_total(current_user),
        "open_tickets_admin": TicketService.open_count_for_admin() if current_user.is_global_admin else 0,
    })


# ---------------------------------------------------------------------
# Zgłoszenia problemów ("Zgłoś problem") — zakładka "Zgłoszenia" w panelu
# czatu. Zwykły user widzi/tworzy swoje zgłoszenia, globalny admin widzi
# i obsługuje wszystkie (patrz TicketService.get_visible_tickets).
# ---------------------------------------------------------------------
@chat_bp.route("/tickets")
@login_required
def list_tickets():
    tickets = TicketService.get_visible_tickets(current_user)
    return jsonify({"tickets": [_serialize_ticket(t) for t in tickets]})


@chat_bp.route("/tickets", methods=["POST"])
@login_required
def create_ticket():
    data = request.get_json(silent=True) or request.form
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    if not title or not description:
        return jsonify({"error": "missing_fields"}), 400
    if len(title) > 200:
        return jsonify({"error": "title_too_long"}), 400
    if len(description) > 4000:
        return jsonify({"error": "description_too_long"}), 400
    ticket = TicketService.create_ticket(current_user, title, description)
    return jsonify({"ticket": _serialize_ticket(ticket)})


@chat_bp.route("/tickets/<int:ticket_id>")
@login_required
def ticket_detail(ticket_id: int):
    ticket = _ticket_or_403(ticket_id)
    entries = [_serialize_ticket_entry(ticket, is_first=True)]
    entries += [_serialize_ticket_entry(r) for r in ticket.replies]
    return jsonify({"ticket": _serialize_ticket(ticket), "entries": entries})


@chat_bp.route("/tickets/<int:ticket_id>/replies", methods=["POST"])
@login_required
def add_ticket_reply(ticket_id: int):
    ticket = _ticket_or_403(ticket_id)
    data = request.get_json(silent=True) or request.form
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "empty"}), 400
    if len(content) > 4000:
        return jsonify({"error": "too_long"}), 400
    reply = TicketService.add_reply(ticket, current_user, content)
    return jsonify({
        "entry": _serialize_ticket_entry(reply),
        "ticket": _serialize_ticket(ticket),
    })


@chat_bp.route("/tickets/<int:ticket_id>/status", methods=["POST"])
@login_required
def set_ticket_status(ticket_id: int):
    ticket = _ticket_or_403(ticket_id)
    data = request.get_json(silent=True) or request.form
    status = (data.get("status") or "").strip()
    ok, err = TicketService.set_status(ticket, status, current_user)
    if not ok:
        return jsonify({"error": err}), 403
    return jsonify({"ticket": _serialize_ticket(ticket)})

