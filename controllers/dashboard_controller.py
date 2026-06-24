from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from services.exam_service import ExamService
from services.room_service import RoomService
from services.notification_service import NotificationService
from datetime import datetime

dashboard_bp = Blueprint("dashboard", __name__)

@dashboard_bp.route("/")
@login_required
def index():
    rooms = RoomService.get_user_rooms(current_user)
    upcoming = ExamService.get_user_upcoming_exams(current_user, days=14)
    unread = NotificationService.get_unread_count(current_user)
    now = datetime.utcnow()
    return render_template(
        "dashboard.html",
        rooms=rooms,
        upcoming=upcoming,
        unread=unread,
        now=now,
        current_month=now.month,
        current_year=now.year,
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