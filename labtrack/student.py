"""Экран студента: страница устройства по QR-коду и «Мои сообщения»."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from .auth import current_user, login_required
from .db import categories_for, get_db, reports_query, status_for, utcnow

bp = Blueprint("student", __name__)

MAX_COMMENT = 300


def _device_or_404(code: str):
    dev = get_db().execute("SELECT * FROM devices WHERE code = ? AND is_active = 1", (code.lower(),)).fetchone()
    if dev is None:
        abort(404, description=f"Устройство «{code}» не зарегистрировано. Сообщите лаборанту.")
    return dev


def _open_count(code: str) -> int:
    return get_db().execute(
        "SELECT COUNT(*) FROM reports WHERE device_code = ? AND kind = 'problem' AND resolved = 0", (code,)
    ).fetchone()[0]


def _recent_by_user(code: str, user_id: int, kind: str) -> bool:
    minutes = current_app.config["REPORT_COOLDOWN_MIN"]
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    return get_db().execute(
        "SELECT 1 FROM reports WHERE device_code = ? AND reporter_id = ? AND kind = ? AND created_at > ?",
        (code, user_id, kind, since),
    ).fetchone() is not None


@bp.route("/d/<code>", methods=["GET", "POST"])
@login_required
def device(code):
    dev = _device_or_404(code)
    user = current_user()
    cats = categories_for(dev["type"])
    step = request.args.get("step", "choose")
    errors = {}
    form = {"categories": [], "comment": ""}

    if request.method == "POST":
        action = request.form.get("action")
        if action == "ok":
            if not _recent_by_user(dev["code"], user["id"], "ok"):
                db = get_db()
                db.execute(
                    "INSERT INTO reports (device_code, kind, reporter_id, created_at) VALUES (?, 'ok', ?, ?)",
                    (dev["code"], user["id"], utcnow()),
                )
                db.commit()
            return redirect(url_for("student.device", code=dev["code"], step="sent-ok"))

        if action == "problem":
            step = "problem"
            allowed = {c["code"] for c in cats}
            chosen = [c for c in request.form.getlist("categories") if c in allowed]
            comment = request.form.get("comment", "").strip()[:MAX_COMMENT]
            form = {"categories": chosen, "comment": comment}
            if not chosen:
                errors["categories"] = "Выберите хотя бы один вариант."
            elif _recent_by_user(dev["code"], user["id"], "problem"):
                errors["categories"] = (
                    f"Вы уже сообщали о проблеме с {dev['name']} в последние "
                    f"{current_app.config['REPORT_COOLDOWN_MIN']} минут. Лаборант её видит."
                )
            if not errors:
                db = get_db()
                cur = db.execute(
                    "INSERT INTO reports (device_code, kind, comment, reporter_id, created_at) "
                    "VALUES (?, 'problem', ?, ?, ?)",
                    (dev["code"], comment, user["id"], utcnow()),
                )
                db.executemany(
                    "INSERT INTO report_categories (report_id, category_code) VALUES (?, ?)",
                    [(cur.lastrowid, c) for c in chosen],
                )
                db.commit()
                return redirect(url_for("student.device", code=dev["code"], step="sent-problem"))

    if step not in {"choose", "problem", "sent-ok", "sent-problem"}:
        step = "choose"
    n_open = _open_count(dev["code"])
    return render_template(
        "student/device.html", dev=dev, cats=cats, step=step, errors=errors, form=form,
        open_count=n_open, status=status_for(n_open),
    )


@bp.route("/me")
@login_required
def home():
    user = current_user()
    mine = reports_query("WHERE r.reporter_id = ?", (user["id"],), limit=30)
    return render_template("student/home.html", reports=mine)
