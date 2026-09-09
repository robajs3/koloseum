from app import create_app
from models import User
from services.notification_service import NotificationService

app = create_app()
with app.app_context():
    user = User.query.filter_by(username="testowy").first()
    NotificationService.create_notification(
        user=user,
        title="Test push",
        body="Jeśli to widzisz, wszystko działa!",
    )