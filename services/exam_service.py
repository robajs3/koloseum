from models import db, Exam, Subject, RoomMember, User, Notification
from datetime import datetime, timedelta
from services.notification_service import NotificationService


class ExamService:

    @staticmethod
    def create_exam(subject: Subject, title: str, description: str,
                    exam_date: datetime, location: str, exam_type: str,
                    creator: User) -> Exam:
        exam = Exam(
            subject_id=subject.id,
            title=title,
            description=description,
            exam_date=exam_date,
            location=location,
            exam_type=exam_type,
            created_by=creator.id,
        )
        db.session.add(exam)
        db.session.commit()

        # Notify all room members
        NotificationService.notify_new_exam(exam)
        return exam

    @staticmethod
    def update_exam(exam: Exam, title: str, description: str,
                    exam_date: datetime, location: str, exam_type: str) -> Exam:
        exam.title = title
        exam.description = description
        exam.exam_date = exam_date
        exam.location = location
        exam.exam_type = exam_type
        exam.updated_at = datetime.utcnow()
        db.session.commit()
        return exam

    @staticmethod
    def delete_exam(exam: Exam) -> None:
        db.session.delete(exam)
        db.session.commit()

    @staticmethod
    def get_user_upcoming_exams(user: User, days: int = 30) -> list[Exam]:
        """Get upcoming exams for all subjects in all user's rooms."""
        memberships = RoomMember.query.filter_by(user_id=user.id).all()
        room_ids = [m.room_id for m in memberships]
        subjects = Subject.query.filter(Subject.room_id.in_(room_ids)).all()
        subject_ids = [s.id for s in subjects]
        now = datetime.utcnow()
        until = now + timedelta(days=days)
        return (
            Exam.query
            .filter(Exam.subject_id.in_(subject_ids))
            .filter(Exam.exam_date >= now)
            .filter(Exam.exam_date <= until)
            .order_by(Exam.exam_date.asc())
            .all()
        )

    @staticmethod
    def get_calendar_events(user: User, year: int, month: int) -> list[dict]:
        """Return events for the calendar view."""
        memberships = RoomMember.query.filter_by(user_id=user.id).all()
        room_ids = [m.room_id for m in memberships]
        subjects = Subject.query.filter(Subject.room_id.in_(room_ids)).all()
        subject_ids = [s.id for s in subjects]

        from datetime import date
        import calendar as cal_module
        _, last_day = cal_module.monthrange(year, month)
        start = datetime(year, month, 1)
        end = datetime(year, month, last_day, 23, 59, 59)

        exams = (
            Exam.query
            .filter(Exam.subject_id.in_(subject_ids))
            .filter(Exam.exam_date >= start)
            .filter(Exam.exam_date <= end)
            .all()
        )

        events = []
        for e in exams:
            events.append({
                "id": e.id,
                "title": e.title,
                "date": e.exam_date.strftime("%Y-%m-%d"),
                "time": e.exam_date.strftime("%H:%M"),
                "type": e.exam_type,
                "subject": e.subject.name,
                "subject_color": e.subject.color,
                "location": e.location,
            })
        return events