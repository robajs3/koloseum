import json
from models import db, Notification, RoomMember, User
from flask import current_app


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
        NotificationService._push_to_browser(user, title, body, link)
        return n

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
            webpush(
                subscription_info=subscription_info,
                data=json.dumps({"title": title, "body": body, "link": link or "/koloseum/"}),
                vapid_private_key=vapid_private,
                vapid_claims=vapid_claims,
            )
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
                body=f"{subject.name} — {exam.exam_date.strftime('%d.%m.%Y %H:%M')}",
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