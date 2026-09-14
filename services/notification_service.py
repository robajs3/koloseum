import json
from datetime import timedelta
from models import db, Notification, RoomMember, User, Exam, ExamReminderLog
from flask import current_app
from utils.timezone import utc_now, to_local


class NotificationService:

    @staticmethod
    def create_notification(user: User, title: str, body: str,
                            notif_type: str = "info", link: str = None) -> Notification:
        n = Notification(
            user_id=user.id,
            title=title,
            body=body,
            type=notif_type,
            link=link,
        )
        db.session.add(n)
        db.session.commit()
        # Powiadomienie zawsze ląduje w panelu (n wyżej) — push wysyłamy
        # tylko jeśli user go aktualnie nie wyciszył (patrz is_muted).
        if not NotificationService.is_muted(user):
            NotificationService._push_to_browser(user, title, body, link)
        return n

    @staticmethod
    def is_muted(user: User) -> bool:
        """Czy user ma aktualnie aktywne 'Wycisz powiadomienia'."""
        return bool(user.muted_until) and user.muted_until > utc_now()

    @staticmethod
    def mute_for(user: User, minutes: int) -> None:
        """Wycisza powiadomienia push na `minutes` minut (max 1 dzień —
        pilnowane też po stronie kontrolera)."""
        minutes = max(1, min(minutes, 24 * 60))
        user.muted_until = utc_now() + timedelta(minutes=minutes)
        db.session.commit()

    @staticmethod
    def unmute(user: User) -> None:
        user.muted_until = None
        db.session.commit()

    @staticmethod
    def _push_to_browser(user: User, title: str, body: str, link: str = None):
        if not user.notifications_enabled or not user.push_subscription:
            return
        try:
            from pywebpush import webpush, WebPushException
            subscription_info = json.loads(user.push_subscription)
            vapid_private = current_app.config.get("VAPID_PRIVATE_KEY")
            vapid_claims = {
                "sub": f"mailto:{current_app.config.get('VAPID_CLAIMS_EMAIL', 'admin@koloseum.local')}"
            }
            if not vapid_private:
                return
            prefix = current_app.config.get("PREFIX", "/koloseum")
            webpush(
                subscription_info=subscription_info,
                data=json.dumps({"title": title, "body": body, "link": link or f"{prefix}/"}),
                vapid_private_key=vapid_private,
                vapid_claims=vapid_claims,
            )
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                # Subskrypcja wygasła / przeglądarka ją unregisterowała (np.
                # po wcześniejszym niedziałającym SW pod Tailscale) —
                # czyścimy ją, żeby nie zaśmiecać logów przy każdym powiadomieniu.
                user.push_subscription = None
                db.session.commit()
            current_app.logger.warning(f"Push notification failed for user {user.id}: {e}")
        except Exception as e:
            current_app.logger.warning(f"Push notification failed for user {user.id}: {e}")

    @staticmethod
    def notify_new_exam(exam) -> None:
        from models import Subject
        subject = Subject.query.get(exam.subject_id)
        if not subject:
            return
        members = RoomMember.query.filter_by(room_id=subject.room_id).all()
        prefix = current_app.config.get("PREFIX", "/koloseum")
        for m in members:
            user = m.user
            if not user.notifications_enabled or not user.notify_new_exam:
                continue
            NotificationService.create_notification(
                user=user,
                title=f"Nowe kolokwium: {exam.title}",
                body=f"{subject.name} — {to_local(exam.exam_date).strftime('%d.%m.%Y %H:%M')}",
                notif_type="exam",
                link=f"{prefix}/subjects/{subject.id}",
            )

    @staticmethod
    def mark_read(notification_id: int, user: User) -> bool:
        n = Notification.query.filter_by(id=notification_id, user_id=user.id).first()
        if not n:
            return False
        n.is_read = True
        db.session.commit()
        return True

    @staticmethod
    def mark_all_read(user: User) -> None:
        Notification.query.filter_by(user_id=user.id, is_read=False).update({"is_read": True})
        db.session.commit()

    @staticmethod
    def get_unread_count(user: User) -> int:
        return Notification.query.filter_by(user_id=user.id, is_read=False).count()

    @staticmethod
    def save_push_subscription(user: User, subscription: dict) -> None:
        user.push_subscription = json.dumps(subscription)
        db.session.commit()

    @staticmethod
    def check_and_send_exam_reminders() -> None:
        """Wywoływane cyklicznie przez wątek w services/scheduler.py.

        Sprawdza wszystkie nadchodzące terminy i wysyła:
        - przypomnienie "godzinę przed" (user.notify_hour_before), gdy do
          egzaminu zostało między 0 a 65 minut,
        - przypomnienie "X dni przed" (user.notify_exam_reminder,
          user.notify_days_before), gdy do egzaminu zostało mniej niż
          X dni (a wciąż więcej niż godzina, żeby nie dublować z
          powyższym).

        Każde przypomnienie jest wysyłane co najwyżej raz na
        (exam, user, rodzaj) — pilnuje tego ExamReminderLog z unikalnym
        constraintem, więc bezpiecznie jest wołać tę funkcję z kilku
        procesów/workerów naraz.
        """
        now = utc_now()
        horizon = now + timedelta(days=31)  # notify_days_before ma max 30
        upcoming = (
            Exam.query
            .filter(Exam.exam_date >= now)
            .filter(Exam.exam_date <= horizon)
            .all()
        )
        for exam in upcoming:
            subject = exam.subject
            if not subject:
                continue
            members = RoomMember.query.filter_by(room_id=subject.room_id).all()
            delta = exam.exam_date - now
            for m in members:
                user = m.user
                if not user or not user.notifications_enabled:
                    continue

                if user.notify_hour_before and timedelta(0) <= delta <= timedelta(minutes=65):
                    NotificationService._send_reminder_once(
                        exam, user, kind="hour",
                        title=f"Za godzinę: {exam.title}",
                        body=f"{subject.name} — {to_local(exam.exam_date).strftime('%H:%M')}",
                    )

                if (user.notify_exam_reminder and delta > timedelta(minutes=65)
                        and delta <= timedelta(days=user.notify_days_before or 1)):
                    NotificationService._send_reminder_once(
                        exam, user, kind="days",
                        title=f"Zbliża się termin: {exam.title}",
                        body=f"{subject.name} — {to_local(exam.exam_date).strftime('%d.%m.%Y %H:%M')}",
                    )

    @staticmethod
    def _send_reminder_once(exam: Exam, user: User, kind: str, title: str, body: str) -> None:
        log = ExamReminderLog(exam_id=exam.id, user_id=user.id, kind=kind)
        db.session.add(log)
        try:
            db.session.commit()
        except Exception:
            # Już wysłane wcześniej (constraint uq_exam_reminder_once) —
            # albo przez ten sam wątek w poprzednim ticku, albo przez
            # inny worker gunicorna. Nic więcej nie robimy.
            db.session.rollback()
            return

        from flask import current_app
        prefix = current_app.config.get("PREFIX", "/koloseum")
        NotificationService.create_notification(
            user=user,
            title=title,
            body=body,
            notif_type="reminder",
            link=f"{prefix}/subjects/{exam.subject_id}",
        )