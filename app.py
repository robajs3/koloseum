import os
import secrets
from datetime import date, datetime
from flask import Flask, render_template, redirect, url_for, abort, send_from_directory, jsonify
from flask_login import LoginManager, current_user, login_user
from config import Config
from models import db, User
from controllers import auth_bp, dashboard_bp, room_bp, subject_bp, profile_bp, notification_bp, admin_bp, export_api_bp, filevault_panel_bp, chat_bp
from utils.timezone import to_local
import sso_client


def _create_koloseum_user(hub_username: str):
    """Zakłada w Koloseum nowe lokalne konto dla usera z LoginHub, który
    jeszcze nie miał tu żadnego konta. Wywoływane przez
    sso_client.resolve_or_create_local_user przy pierwszej wizycie.

    Hasło jest losowe i nieznane nikomu — to konto loguje się wyłącznie
    przez SSO (chyba że user później zmieni hasło z poziomu profilu).
    get-or-create po username, żeby powtórne wywołanie (np. gdy zgłoszenie
    do Huba nie doszło za pierwszym razem) nie tworzyło duplikatu.
    """
    existing = User.query.filter_by(username=hub_username).first()
    if existing:
        return existing.id, existing.username

    username = hub_username
    suffix = 1
    while User.query.filter_by(username=username).first():
        suffix += 1
        username = f"{hub_username}{suffix}"

    # email jest w Koloseum wymagany i unikalny, a Hub go nie zna —
    # generujemy placeholder, użytkownik może go później zmienić w profilu.
    email = f"{username}@sso.local"
    while User.query.filter_by(email=email).first():
        suffix += 1
        email = f"{hub_username}{suffix}@sso.local"

    user = User(username=username, email=email)
    user.set_password(secrets.token_urlsafe(24))
    db.session.add(user)
    db.session.commit()
    return user.id, user.username


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Extensions
    db.init_app(app)

    # Filtr Jinja do konwersji dat z bazy (UTC) na czas lokalny przy
    # wyświetlaniu — patrz utils/timezone.py. Użycie w szablonach:
    # {{ (obj.created_at|localtime).strftime('%H:%M') }}
    app.jinja_env.filters["localtime"] = to_local

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
    # LoginHub. Jeśli jego konto jest już połączone z koloseum — zaloguj go
    # lokalnie. Jeśli NIE jest jeszcze połączone — resolve_or_create_local_user
    # samo zakłada tu dla niego nowe konto (_create_koloseum_user) i zgłasza
    # połączenie do Huba, więc nie trzeba czekać na ręczne sparowanie w /admin.
    # Zwykłe logowanie hasłem zostaje bez zmian jako plan B.
    @app.before_request
    def _sso_autologin():
        if current_user.is_authenticated:
            return
        local_id = sso_client.resolve_or_create_local_user(
            app_slug="koloseum", create_user=_create_koloseum_user
        )
        if local_id:
            user = User.query.get(local_id)
            if user:
                login_user(user)

    @login_manager.unauthorized_handler
    def _unauthorized():
        from flask import request
        # UWAGA (zweryfikowane w praktyce, patrz PrefixMiddleware niżej):
        # Tailscale Serve --set-path ŚCINA prefiks /koloseum zanim żądanie
        # trafi do Flaska, więc request.path go NIE zawiera. PrefixMiddleware
        # wpisuje prefiks do SCRIPT_NAME, więc request.script_root go ma —
        # doklejamy ręcznie, inaczej LoginHub po zalogowaniu odeśle poza
        # appkę (np. na /dashboard/ zamiast /koloseum/dashboard/).
        next_path = request.script_root + request.path
        return redirect(sso_client.login_url(next_path))

    # Blueprints — BEZ url_prefix: Tailscale ściera /koloseum zanim żądanie
    # trafi tutaj, więc trasy muszą być dopasowywane po ścieżce bez prefiksu
    # (patrz create_wsgi_app/PrefixMiddleware — prefiks jest doklejany tylko
    # do generowanych linków przez SCRIPT_NAME, nigdy do dopasowania trasy).
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(room_bp)
    app.register_blueprint(subject_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(notification_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(export_api_bp)
    app.register_blueprint(filevault_panel_bp)
    app.register_blueprint(chat_bp)

    # Root redirect
    @app.route("/")
    def root():
        return redirect(url_for("dashboard.index"))

    # Service Worker — musi być serwowany z roota, żeby scope obejmował całą appkę
    @app.route("/sw.js")
    def service_worker():
        response = send_from_directory("static/js", "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    # Web App Manifest — generowany dynamicznie, bo start_url/scope MUSZĄ być
    # bezwzględnymi ścieżkami widzianymi przez przeglądarkę (z prefiksem
    # /koloseum), inaczej instalacja jako PWA (i push na iOS Safari, który
    # wymaga appki dodanej do ekranu głównego) się nie uda. Statyczny plik nie
    # mógłby zawrzeć PREFIX bez przebudowy przy każdej zmianie konfiguracji.
    @app.route("/manifest.json")
    def web_app_manifest():
        prefix = (app.config.get("PREFIX") or "").rstrip("/")
        manifest = {
            "name": "Koloseum",
            "short_name": "Koloseum",
            "description": "Zarządzanie terminami i kolokwiami dla studentów",
            "start_url": f"{prefix}/",
            "scope": f"{prefix}/",
            "display": "standalone",
            "orientation": "portrait-primary",
            "background_color": "#0f1115",
            "theme_color": "#4f46e5",
            "icons": [
                {"src": f"{prefix}/static/img/icon-72.png", "sizes": "72x72", "type": "image/png", "purpose": "any"},
                {"src": f"{prefix}/static/img/icon-96.png", "sizes": "96x96", "type": "image/png", "purpose": "any"},
                {"src": f"{prefix}/static/img/icon-144.png", "sizes": "144x144", "type": "image/png", "purpose": "any"},
                {"src": f"{prefix}/static/img/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                {"src": f"{prefix}/static/img/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            ],
        }
        response = jsonify(manifest)
        response.headers["Content-Type"] = "application/manifest+json"
        return response

    # Template globals
    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        unread = 0
        unread_dm = 0
        if current_user.is_authenticated:
            from services.notification_service import NotificationService
            from services.chat_service import ChatService
            unread = NotificationService.get_unread_count(current_user)
            unread_dm = ChatService.unread_dm_total(current_user)
        return {
            "now": datetime.utcnow(),
            # Prefiks widziany przez PRZEGLĄDARKĘ (do budowania linków w JS,
            # patrz static/js/app.js: fetch(window.PREFIX + '/...')) — to
            # NIE jest to samo co wewnętrzny routing Flaska, który jest bez
            # prefiksu (patrz wyżej).
            "prefix": app.config.get("PREFIX", "/koloseum"),
            "filevault_url": app.config.get("FILEVAULT_BASE_URL", "/filevault"),
            "unread_notifications": unread,
            "unread_dm": unread_dm,
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

    # Wątek w tle do przypomnień "godzinę przed" / "X dni przed" — patrz
    # services/scheduler.py (bezpieczny nawet przy kilku workerach gunicorna).
    from services.scheduler import start_reminder_scheduler
    start_reminder_scheduler(app)


def create_wsgi_app(config_class=Config):
    """
    Buduje finalną aplikację WSGI z doklejonym prefiksem w SCRIPT_NAME.

    WAŻNE (zweryfikowane w praktyce — patrz historia debugowania pętli
    przekierowań): Tailscale Serve z opcją --set-path ŚCINA prefiks z URL-a
    zanim przekaże request dalej do backendu — backend dostaje ścieżkę BEZ
    prefiksu (np. /dashboard, a nie /koloseum/dashboard). Dlatego routing we
    Flasku jest bez prefiksu (blueprinty rejestrowane bez url_prefix), a
    jedyne co trzeba doklejić to prefiks w generowanych linkach (url_for,
    redirecty) — inaczej przeglądarka "wypadnie" spod /koloseum przy
    pierwszym kliknięciu/przekierowaniu, dokładnie tak jak w buggu z pętlą
    na "/".

    Realizujemy to przez ustawienie WSGI environ["SCRIPT_NAME"] = PREFIX,
    NIE ruszając PATH_INFO — Flask użyje SCRIPT_NAME do generowania
    poprawnych, prefiksowanych adresów, a dopasowywanie tras dalej odbywa
    się na (nieprefiksowanym) PATH_INFO, dokładnie tak jak przychodzi
    z Tailscale.
    """
    flask_app = create_app(config_class)
    init_db(flask_app)

    prefix = (flask_app.config.get("PREFIX") or "").rstrip("/")
    if not prefix:
        return flask_app

    if not prefix.startswith("/"):
        prefix = "/" + prefix

    return PrefixMiddleware(flask_app, prefix)


class PrefixMiddleware:
    """Dokleja prefiks do SCRIPT_NAME, nie ruszając PATH_INFO.

    Używane, gdy reverse proxy (Tailscale Serve --set-path) ściera prefiks
    z requestu zanim ten trafi do appki, ale appka i tak ma generować
    linki/przekierowania z tym prefiksem, żeby przeglądarka została pod
    właściwym adresem.
    """

    def __init__(self, app, prefix):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        environ["SCRIPT_NAME"] = self.prefix
        return self.app(environ, start_response)


if __name__ == "__main__":
    application = create_wsgi_app()
    if isinstance(application, Flask):
        application.run(host="0.0.0.0", port=5001, debug=False)
    else:
        # Aplikacja owinięta w PrefixMiddleware (ustawiony PREFIX) — do
        # lokalnego dev-runa i tak trzeba jej użyć jako WSGI callable.
        from werkzeug.serving import run_simple
        run_simple("0.0.0.0", 5001, application)
