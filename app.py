import os
from datetime import date, datetime
from flask import Flask, render_template, redirect, abort, send_from_directory
from flask_login import LoginManager, current_user, login_user
from config import Config
from models import db, User
from controllers import auth_bp, dashboard_bp, room_bp, subject_bp, profile_bp, notification_bp, admin_bp, export_api_bp
import sso_client

PREFIX = "/koloseum"


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Extensions
    db.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Zaloguj się, aby uzyskać dostęp."
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # --- SSO (LoginHub) ---------------------------------------------------
    # Jeśli user nie jest zalogowany lokalnie, sprawdź czy ma ważne ciasteczko
    # LoginHub i czy jego konto jest połączone z koloseum. Jeśli tak — zaloguj
    # go lokalnie, tak jakby przeszedł przez /koloseum/login. Zwykłe logowanie
    # hasłem zostaje bez zmian jako plan B.
    @app.before_request
    def _sso_autologin():
        if current_user.is_authenticated:
            return
        local_id = sso_client.resolve_local_user_id(app_slug="koloseum")
        if local_id:
            user = User.query.get(local_id)
            if user:
                login_user(user)

    @login_manager.unauthorized_handler
    def _unauthorized():
        from flask import request
        # Uwaga: w przeciwieństwie do niektórych appek dockerowych, koloseum
        # ma prefiks /koloseum wpisany na stałe w url_prefix blueprintów, a
        # nie ścinany przez Tailscale, więc request.path już go zawiera —
        # nie trzeba doklejać script_root.
        return redirect(sso_client.login_url(request.path))

    # Blueprints
    app.register_blueprint(auth_bp,          url_prefix=PREFIX)
    app.register_blueprint(dashboard_bp,     url_prefix=PREFIX)
    app.register_blueprint(room_bp,          url_prefix=PREFIX)
    app.register_blueprint(subject_bp,       url_prefix=PREFIX)
    app.register_blueprint(profile_bp,       url_prefix=PREFIX)
    app.register_blueprint(notification_bp,  url_prefix=PREFIX)
    app.register_blueprint(admin_bp,         url_prefix=PREFIX + "/admin")
    app.register_blueprint(export_api_bp,    url_prefix=PREFIX)

    # Root redirect
    @app.route("/")
    def root():
        return redirect(PREFIX + "/")

    # Service Worker — musi być serwowany z roota, żeby scope obejmował całą appkę
    @app.route("/sw.js")
    def service_worker():
        response = send_from_directory("static/js", "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    # Template globals
    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        unread = 0
        if current_user.is_authenticated:
            from services.notification_service import NotificationService
            unread = NotificationService.get_unread_count(current_user)
        return {
            "now": datetime.utcnow(),
            "prefix": PREFIX,
            "filevault_url": app.config.get("FILEVAULT_BASE_URL", "/filevault"),
            "unread_notifications": unread,
        }

    # Error handlers
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, message="Brak dostępu."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="Nie znaleziono strony."), 404

    @app.errorhandler(413)
    def too_large(e):
        return render_template("error.html", code=413,
                               message="Aby przesyłać pliki większe niż 100MB skorzystaj z FileVault."), 413

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", code=500, message="Błąd serwera."), 500

    return app


def init_db(app: Flask) -> None:
    with app.app_context():
        db.create_all()
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "chat"), exist_ok=True)
        os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "materials"), exist_ok=True)


if __name__ == "__main__":
    application = create_app()
    init_db(application)
    application.run(host="0.0.0.0", port=5001, debug=False)