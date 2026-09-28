"""LabTrack — учёт оборудования компьютерного класса с QR-кодами."""
import os
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, abort, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db as dbmod


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    return default if val is None else val.strip().lower() in {"1", "true", "yes", "on"}


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    secret = os.environ.get("SECRET_KEY")
    app.config.update(
        SECRET_KEY=secret or secrets.token_hex(32),
        DATABASE=os.environ.get("DATABASE_PATH", os.path.join(app.instance_path, "labtrack.sqlite3")),
        TIMEZONE=os.environ.get("TIMEZONE", "Europe/Moscow"),
        ROOM_NUMBER=os.environ.get("ROOM_NUMBER", "52-37"),
        # Код приглашения для регистрации студентов. Пусто — регистрация закрыта.
        REGISTRATION_CODE=os.environ.get("REGISTRATION_CODE", ""),
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", ""),
        PERMANENT_SESSION_LIFETIME=timedelta(days=int(os.environ.get("SESSION_DAYS", "30"))),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_env_bool("SESSION_COOKIE_SECURE", False),
        SESSION_COOKIE_NAME="labtrack_session",
        MAX_CONTENT_LENGTH=64 * 1024,
        LOGIN_MAX_FAILS=5,
        LOGIN_WINDOW_MIN=15,
        REPORT_COOLDOWN_MIN=10,
    )
    if test_config:
        app.config.update(test_config)
    if not secret and not app.config.get("TESTING"):
        app.logger.warning("SECRET_KEY не задан: сессии сбросятся при перезапуске.")

    # за обратным прокси (Caddy/Nginx) — корректные схема и IP клиента
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.teardown_appcontext(dbmod.close_db)

    from . import auth, cli, lab, student

    app.register_blueprint(auth.bp)
    app.register_blueprint(student.bp)
    app.register_blueprint(lab.bp)
    cli.register(app)

    tz = ZoneInfo(app.config["TIMEZONE"])

    @app.template_filter("dt")
    def fmt_dt(iso: str | None, fmt: str = "%d.%m %H:%M") -> str:
        if not iso:
            return "—"
        return datetime.fromisoformat(iso).astimezone(tz).strftime(fmt)

    @app.template_filter("ago")
    def fmt_ago(iso: str | None) -> str:
        if not iso:
            return "—"
        delta = datetime.now(tz) - datetime.fromisoformat(iso).astimezone(tz)
        m = int(delta.total_seconds() // 60)
        if m < 1:
            return "только что"
        if m < 60:
            return f"{m} мин назад"
        h = m // 60
        if h < 24:
            return f"{h} ч назад"
        return f"{h // 24} дн назад"

    @app.template_filter("plural")
    def plural(n: int, forms: str) -> str:
        a, b, c = forms.split(",")
        n = abs(int(n))
        if n % 10 == 1 and n % 100 != 11:
            return a
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return b
        return c

    @app.context_processor
    def inject_globals():
        return {
            "csrf_token": _csrf_token,
            "current_user": auth.current_user(),
            "room_number": app.config["ROOM_NUMBER"],
            "STATUS_LABELS": dbmod.STATUS_LABELS,
            "TYPE_LABELS": dbmod.TYPE_LABELS,
        }

    # --- CSRF: токен в сессии, проверка на каждом POST ---
    def _csrf_token() -> str:
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_urlsafe(32)
        return session["_csrf"]

    @app.before_request
    def csrf_protect():
        if request.method == "POST" and not app.config.get("WTF_CSRF_DISABLED"):
            sent = request.form.get("csrf_token", "")
            if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
                abort(400, description="Форма устарела. Обновите страницу и отправьте ещё раз.")

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.route("/")
    def index():
        user = auth.current_user()
        if user is None:
            return redirect(url_for("auth.login"))
        if user["role"] == "lab":
            return redirect(url_for("lab.dashboard"))
        return redirect(url_for("student.home"))

    @app.route("/healthz")
    def healthz():
        dbmod.get_db().execute("SELECT 1")
        return {"status": "ok"}

    for code, title in [(400, "Запрос не выполнен"), (403, "Нет доступа"), (404, "Страница не найдена")]:
        app.register_error_handler(
            code,
            lambda e, code=code, title=title: (
                render_template("error.html", code=code, title=title, message=getattr(e, "description", "")),
                code,
            ),
        )

    with app.app_context():
        dbmod.init_db()

    return app
