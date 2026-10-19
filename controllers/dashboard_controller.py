from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from services.exam_service import ExamService
from services.room_service import RoomService
from services.notification_service import NotificationService
from models.announcement_model import Announcement
from models import Subject, normalize_exam_type
from utils.timezone import to_utc
from datetime import datetime

dashboard_bp = Blueprint("dashboard", __name__)

@dashboard_bp.route("/")
@login_required
def index():
    rooms = RoomService.get_user_rooms(current_user)
    upcoming = ExamService.get_user_upcoming_exams(current_user, days=14)
    unread = NotificationService.get_unread_count(current_user)
    now = datetime.utcnow()
    active_announcements = [a for a in Announcement.query.filter_by(is_active=True).order_by(Announcement.created_at.desc()).all() if a.is_visible]
    return render_template(
        "dashboard.html",
        rooms=rooms,
        upcoming=upcoming,
        unread=unread,
        now=now,
        current_month=now.month,
        current_year=now.year,
        announcements=active_announcements,
    )

@dashboard_bp.route("/calendar/events")
@login_required
def calendar_events():
    try:
        year = int(request.args.get("year", datetime.utcnow().year))
        month = int(request.args.get("month", datetime.utcnow().month))
    except ValueError:
        return jsonify([])
    events = ExamService.get_calendar_events(current_user, year, month)
    return jsonify(events)

@dashboard_bp.route("/privacy")
def privacy():
    return render_template("privacy.html")

@dashboard_bp.route("/calendar/subjects")
@login_required
def calendar_subjects():
    """Przedmioty, do których aktualny user może dodać termin z kalendarza."""
    subjects = ExamService.get_creatable_subjects(current_user)
    return jsonify([{"id": s.id, "name": s.name} for s in subjects])


@dashboard_bp.route("/calendar/events/create", methods=["POST"])
@login_required
def calendar_create_event():
    """Dodanie terminu na konkretny dzień prosto z kalendarza (JSON)."""
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:256]
    date_str = (data.get("date") or "").strip()
    time_str = (data.get("time") or "").strip() or "08:00"
    if not title or not date_str:
        return jsonify({"error": "Podaj tytuł i datę."}), 400
    try:
        subject_id = int(data.get("subject_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Wybierz przedmiot."}), 400
    try:
        exam_date = to_utc(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
    except ValueError:
        return jsonify({"error": "Nieprawidłowa data lub godzina."}), 400

    allowed = {s.id: s for s in ExamService.get_creatable_subjects(current_user)}
    subject = allowed.get(subject_id)
    if not subject:
        return jsonify({"error": "Brak uprawnień do dodawania terminów w tym przedmiocie."}), 403

    ExamService.create_exam(
        subject,
        title,
        (data.get("description") or "").strip()[:512],
        exam_date,
        (data.get("location") or "").strip()[:256],
        normalize_exam_type(data.get("exam_type")),
        current_user,
    )
    return jsonify({"ok": True})
