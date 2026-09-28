"""Раздел лаборанта: дашборд, история устройств, наклейки, пользователи."""
import re
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   url_for)

from . import qr
from .auth import USERNAME_RE, MIN_PASSWORD, create_user, current_user, generate_temp_password, lab_required, set_password
from .db import (TYPE_LABELS, devices_with_status, get_db, get_setting, reports_query, set_setting,
                 status_for, utcnow)

bp = Blueprint("lab", __name__, url_prefix="/lab")

CODE_RE = re.compile(r"^(pc|printer|projector)-[0-9]{1,3}$")


@bp.route("/")
@lab_required
def dashboard():
    devices = devices_with_status()
    db = get_db()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    week = db.execute(
        "SELECT COUNT(*) FROM reports WHERE kind != 'service' AND created_at > ?", (week_ago,)
    ).fetchone()[0]
    by_cat = db.execute(
        """
        SELECT c.title, COUNT(*) AS n
        FROM reports r JOIN report_categories rc ON rc.report_id = r.id
        JOIN categories c ON c.code = rc.category_code
        WHERE r.kind = 'problem' AND r.resolved = 0
        GROUP BY c.code ORDER BY n DESC, c.title
        """
    ).fetchall()
    attention = sorted((d for d in devices if d["open_count"]),
                       key=lambda d: (-d["open_count"], d["last_problem_at"] or ""))
    return render_template(
        "lab/dashboard.html",
        pcs=[d for d in devices if d["type"] == "pc"],
        others=[d for d in devices if d["type"] != "pc"],
        total=len(devices),
        bad=sum(1 for d in devices if d["open_count"]),
        open_total=sum(d["open_count"] for d in devices),
        week=week,
        attention=attention,
        by_cat=by_cat,
        by_cat_max=max((r["n"] for r in by_cat), default=1),
        feed=reports_query(limit=12),
    )


def _device_or_404(code):
    dev = get_db().execute("SELECT * FROM devices WHERE code = ?", (code,)).fetchone()
    if dev is None:
        abort(404, description=f"Устройство «{code}» не найдено.")
    return dev


@bp.route("/device/<code>")
@lab_required
def device(code):
    dev = _device_or_404(code)
    db = get_db()
    n_open = db.execute(
        "SELECT COUNT(*) FROM reports WHERE device_code = ? AND kind = 'problem' AND resolved = 0", (code,)
    ).fetchone()[0]
    last = {k: db.execute(
        "SELECT MAX(created_at) FROM reports WHERE device_code = ? AND kind = ?", (code, k)
    ).fetchone()[0] for k in ("ok", "service")}
    history = reports_query("WHERE r.device_code = ?", (code,), limit=200)
    return render_template("lab/device.html", dev=dev, open_count=n_open, status=status_for(n_open),
                           last=last, history=history)


@bp.route("/device/<code>/resolve", methods=["POST"])
@lab_required
def resolve(code):
    dev = _device_or_404(code)
    note = request.form.get("note", "").strip()[:300] or "Неисправность устранена"
    user = current_user()
    now = utcnow()
    db = get_db()
    with db:  # одна транзакция
        cur = db.execute(
            "UPDATE reports SET resolved = 1, resolved_at = ?, resolved_by = ? "
            "WHERE device_code = ? AND kind = 'problem' AND resolved = 0",
            (now, user["id"], code),
        )
        db.execute(
            "INSERT INTO reports (device_code, kind, comment, reporter_id, created_at, resolved, resolved_at, resolved_by) "
            "VALUES (?, 'service', ?, ?, ?, 1, ?, ?)",
            (code, note, user["id"], now, now, user["id"]),
        )
    flash(f"{dev['name']}: закрыто жалоб — {cur.rowcount}, запись об обслуживании добавлена.", "ok")
    return redirect(url_for("lab.device", code=code))


@bp.route("/device/<code>/edit", methods=["POST"])
@lab_required
def edit_device(code):
    _device_or_404(code)
    name = request.form.get("name", "").strip()[:40]
    place = request.form.get("place", "").strip()[:60]
    inv = request.form.get("inventory_no", "").strip()[:32]
    active = 1 if request.form.get("is_active") == "on" else 0
    if not name or not inv:
        flash("Название и инвентарный номер обязательны.", "error")
    else:
        db = get_db()
        try:
            db.execute("UPDATE devices SET name = ?, place = ?, inventory_no = ?, is_active = ? WHERE code = ?",
                       (name, place, inv, active, code))
            db.commit()
            flash("Данные устройства сохранены.", "ok")
        except Exception:
            db.rollback()
            flash("Инвентарный номер уже используется другим устройством.", "error")
    return redirect(url_for("lab.device", code=code))


@bp.route("/devices", methods=["GET", "POST"])
@lab_required
def devices():
    db = get_db()
    form = {k: request.form.get(k, "").strip() for k in ("code", "name", "inventory_no", "place")}
    if request.method == "POST":
        code = form["code"].lower()
        if not CODE_RE.match(code):
            flash("Код устройства: pc-N, printer-N или projector-N, например pc-31.", "error")
        elif not form["name"] or not form["inventory_no"]:
            flash("Название и инвентарный номер обязательны.", "error")
        else:
            room = db.execute("SELECT id FROM rooms WHERE number = ?", (current_app.config["ROOM_NUMBER"],)).fetchone()
            if room is None:
                flash("Аудитория не создана. Выполните команду flask seed.", "error")
            else:
                try:
                    db.execute(
                        "INSERT INTO devices (code, room_id, type, name, inventory_no, place, sort_order) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (code, room["id"], code.split("-")[0], form["name"][:40], form["inventory_no"][:32],
                         form["place"][:60], int(code.split("-")[1])),
                    )
                    db.commit()
                    flash(f"Устройство {form['name']} добавлено. Не забудьте напечатать для него наклейку.", "ok")
                    return redirect(url_for("lab.devices"))
                except Exception:
                    db.rollback()
                    flash("Устройство с таким кодом или инвентарным номером уже есть.", "error")
    return render_template("lab/devices.html", devices=devices_with_status(include_inactive=True), form=form)


def _base_url() -> str:
    return get_setting("base_url") or current_app.config["PUBLIC_BASE_URL"] or request.host_url


@bp.route("/stickers", methods=["GET", "POST"])
@lab_required
def stickers():
    if request.method == "POST":
        url = request.form.get("base_url", "").strip()
        if not re.match(r"^https?://[^\s/]+", url):
            flash("Адрес должен начинаться с https://", "error")
        else:
            set_setting("base_url", url.rstrip("/") + "/")
            flash("Адрес сохранён, QR-коды обновлены.", "ok")
        return redirect(url_for("lab.stickers"))
    base = _base_url().rstrip("/") + "/"
    items = [dict(d, url=f"{base}d/{d['code']}") for d in devices_with_status()]
    for d in items:
        d["svg"] = qr.svg(d["url"])
    only = request.args.get("only")
    if only:
        items = [d for d in items if d["code"] == only]
    return render_template("lab/stickers.html", items=items, base=base)


@bp.route("/users", methods=["GET", "POST"])
@lab_required
def users():
    db = get_db()
    me = current_user()
    temp = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            role = request.form.get("role", "student")
            group = request.form.get("study_group", "").strip()
            if role not in {"student", "lab"} or not USERNAME_RE.match(username) or len(full_name) < 3:
                flash("Проверьте логин (латиница, 3–32 символа) и ФИО.", "error")
            elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                flash("Такой логин уже занят.", "error")
            else:
                pwd = generate_temp_password()
                create_user(username, pwd, full_name[:100], role, group[:20])
                temp = (username, pwd)
        else:
            uid = int(request.form.get("user_id", "0") or 0)
            target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if target is None:
                abort(404)
            if target["id"] == me["id"] and action in {"block", "reset"}:
                flash("Свою учётную запись меняйте через «Сменить пароль».", "error")
            elif action == "block":
                db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (uid,))
                db.commit()
                flash(f"{target['full_name']} заблокирован(а).", "ok")
            elif action == "unblock":
                db.execute("UPDATE users SET is_active = 1 WHERE id = ?", (uid,))
                db.commit()
                flash(f"{target['full_name']} разблокирован(а).", "ok")
            elif action == "reset":
                pwd = generate_temp_password()
                set_password(uid, pwd)
                temp = (target["username"], pwd)
        if temp is None:
            return redirect(url_for("lab.users"))
    rows = db.execute(
        """
        SELECT u.*, (SELECT COUNT(*) FROM reports r WHERE r.reporter_id = u.id) AS n_reports
        FROM users u ORDER BY u.role DESC, u.study_group, u.full_name
        """
    ).fetchall()
    return render_template("lab/users.html", users=rows, temp=temp, min_password=MIN_PASSWORD,
                           registration_open=bool(current_app.config["REGISTRATION_CODE"]))
