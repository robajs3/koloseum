from controllers.auth_controller import auth_bp
from controllers.dashboard_controller import dashboard_bp
from controllers.room_controller import room_bp
from controllers.subject_controller import subject_bp
from controllers.profile_controller import profile_bp
from controllers.notification_controller import notification_bp
from controllers.admin_controller import admin_bp

__all__ = [
    "auth_bp",
    "dashboard_bp",
    "room_bp",
    "subject_bp",
    "profile_bp",
    "notification_bp",
    "admin_bp",
]