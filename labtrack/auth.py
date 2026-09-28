"""Вход, выход, регистрация студентов, проверка ролей."""
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, utcnow

bp = Blueprint("auth", __name__, url_prefix="/auth")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8


# ---------- текущий пользователь ----------
def current_user():
    if "user" in g:
        return g.user
    g.user = None
    uid = session.get("uid")
    if uid:
        row = get_db().execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (uid,)).fetchone()
        # сессия недействительна, если пароль сменили после входа
        if row and session.get("pwv") == row["password_hash"][-12:]:
            g.user = row
        else:
            session.clear()
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def lab_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user()["role"] != "lab":
            abort(403, description="Этот раздел доступен только лаборанту.")
        return view(*args, **kwargs)
    return wrapped


def _safe_next(target: str | None) -> str | None:
    if not target:
        return None
    parts = urlparse(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def _start_session(user):
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    session["pwv"] = user["password_hash"][-12:]
    db = get_db()
    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utcnow(), user["id"]))
    db.commit()


# ---------- создание пользователей (используется в CLI и формах) ----------
def create_user(username, password, full_name, role, study_group=""):
    db = get_db()
    cur = db.execute(
        "INSERT INTO users (username, password_hash, full_name, study_group, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username.strip(), generate_password_hash(password), full_name.strip(),
         study_group.strip(), role, utcnow()),
    )
    db.commit()
    return cur.lastrowid


def set_password(user_id: int, password: str):
    db = get_db()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), user_id))
    db.commit()


def generate_temp_password() -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


# ---------- защита от подбора пароля ----------
def _too_many_fails(ip: str) -> bool:
    cfg = current_app.config
    since = (datetime.now(timezone.utc) - timedelta(minutes=cfg["LOGIN_WINDOW_MIN"])).isoformat()
    n = get_db().execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND created_at > ?", (ip, since)
    ).fetchone()[0]
    return n >= cfg["LOGIN_MAX_FAILS"]


def _record_fail(ip: str, username: str):
    db = get_db()
    db.execute("INSERT INTO login_attempts (ip, username, created_at) VALUES (?, ?, ?)",
               (ip, username[:64], utcnow()))
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    db.execute("DELETE FROM login_attempts WHERE created_at < ?", (old,))
    db.commit()


# ---------- представления ----------
@bp.route("/login", methods=["GET", "POST"])
def login():
    nxt = _safe_next(request.values.get("next"))
    if current_user() is not None:
        return redirect(nxt or url_for("index"))
    error = None
    username = ""
    if request.method == "POST":
        ip = request.remote_addr or "?"
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if _too_many_fails(ip):
            error = f"Слишком много неудачных попыток. Подождите {current_app.config['LOGIN_WINDOW_MIN']} минут."
        else:
            user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                if not user["is_active"]:
                    error = "Учётная запись заблокирована. Обратитесь к лаборанту."
                else:
                    _start_session(user)
                    return redirect(nxt or url_for("index"))
            else:
                _record_fail(ip, username)
                error = "Неверный логин или пароль."
    return render_template("auth/login.html", error=error, next=nxt, username=username,
                           signup_open=bool(current_app.config["REGISTRATION_CODE"]))


@bp.route("/register", methods=["GET", "POST"])
def register():
    code_required = current_app.config["REGISTRATION_CODE"]
    if not code_required:
        abort(404)
    nxt = _safe_next(request.values.get("next"))
    form = {k: request.form.get(k, "").strip() for k in ("full_name", "study_group", "username", "invite")}
    errors = {}
    if request.method == "POST":
        password = request.form.get("password", "")
        if not secrets.compare_digest(form["invite"].upper(), code_required.upper()):
            errors["invite"] = "Неверный код регистрации. Его сообщает преподаватель или лаборант."
        if len(form["full_name"]) < 3:
            errors["full_name"] = "Укажите фамилию и имя."
        if not form["study_group"]:
            errors["study_group"] = "Укажите учебную группу."
        if not USERNAME_RE.match(form["username"]):
            errors["username"] = "Логин: 3–32 символа, латинские буквы, цифры, точка, дефис, подчёркивание."
        elif get_db().execute("SELECT 1 FROM users WHERE username = ?", (form["username"],)).fetchone():
            errors["username"] = "Такой логин уже занят."
        if len(password) < MIN_PASSWORD:
            errors["password"] = f"Пароль должен быть не короче {MIN_PASSWORD} символов."
        elif password != request.form.get("password2", ""):
            errors["password2"] = "Пароли не совпадают."
        if not errors:
            uid = create_user(form["username"], password, form["full_name"][:100], "student",
                              form["study_group"][:20])
            _start_session(get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())
            flash("Регистрация завершена. Теперь можно отмечать состояние устройств.", "ok")
            return redirect(nxt or url_for("index"))
    return render_template("auth/register.html", form=form, errors=errors, next=nxt)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Вы вышли из системы.", "ok")
    return redirect(url_for("auth.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    errors = {}
    if request.method == "POST":
        user = current_user()
        old, new, new2 = (request.form.get(k, "") for k in ("old", "new", "new2"))
        if not check_password_hash(user["password_hash"], old):
            errors["old"] = "Текущий пароль указан неверно."
        elif len(new) < MIN_PASSWORD:
            errors["new"] = f"Новый пароль должен быть не короче {MIN_PASSWORD} символов."
        elif new != new2:
            errors["new2"] = "Пароли не совпадают."
        if not errors:
            set_password(user["id"], new)
            _start_session(get_db().execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())
            flash("Пароль изменён.", "ok")
            return redirect(url_for("index"))
    return render_template("auth/password.html", errors=errors)
