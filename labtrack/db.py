"""Подключение к SQLite и общие запросы."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g

TYPE_LABELS = {"pc": "Компьютер", "printer": "Принтер", "projector": "Проектор"}
STATUS_LABELS = {"ok": "Исправно", "problem": "Есть жалобы", "crit": "Требует ремонта"}
CRIT_THRESHOLD = 3  # столько открытых жалоб и больше — «Требует ремонта»


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        path = current_app.config["DATABASE"]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    get_db().executescript(schema)


def status_for(open_count: int) -> str:
    if open_count == 0:
        return "ok"
    return "crit" if open_count >= CRIT_THRESHOLD else "problem"


def get_setting(key: str, default: str = "") -> str:
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()


def categories_for(device_type: str):
    return get_db().execute(
        "SELECT code, title FROM categories WHERE type = ? OR type IS NULL "
        "ORDER BY type IS NULL, sort_order",
        (device_type,),
    ).fetchall()


def category_titles() -> dict:
    return {r["code"]: r["title"] for r in get_db().execute("SELECT code, title FROM categories")}


def devices_with_status(include_inactive: bool = False):
    """Все устройства с числом открытых жалоб, статусом и главной категорией."""
    db = get_db()
    rows = db.execute(
        f"""
        SELECT d.*, COUNT(r.id) AS open_count, MAX(r.created_at) AS last_problem_at
        FROM devices d
        LEFT JOIN reports r ON r.device_code = d.code AND r.kind = 'problem' AND r.resolved = 0
        {'' if include_inactive else 'WHERE d.is_active = 1'}
        GROUP BY d.code
        ORDER BY CASE d.type WHEN 'pc' THEN 0 WHEN 'printer' THEN 1 ELSE 2 END, d.sort_order, d.code
        """
    ).fetchall()
    cats = open_category_counts()
    titles = category_titles()
    result = []
    for r in rows:
        item = dict(r)
        item["status"] = status_for(r["open_count"])
        per_dev = cats.get(r["code"], [])
        item["categories"] = [(titles.get(c, c), n) for c, n in per_dev]
        result.append(item)
    return result


def open_category_counts() -> dict:
    """{device_code: [(category_code, count), ...]} по открытым жалобам, по убыванию."""
    out: dict = {}
    for r in get_db().execute(
        """
        SELECT r.device_code, rc.category_code, COUNT(*) AS n
        FROM reports r JOIN report_categories rc ON rc.report_id = r.id
        WHERE r.kind = 'problem' AND r.resolved = 0
        GROUP BY r.device_code, rc.category_code
        ORDER BY n DESC, rc.category_code
        """
    ):
        out.setdefault(r["device_code"], []).append((r["category_code"], r["n"]))
    return out


def reports_query(where: str = "", params: tuple = (), limit: int = 50):
    """Отчёты с именем устройства, автора и списком категорий."""
    db = get_db()
    rows = db.execute(
        f"""
        SELECT r.*, d.name AS device_name, u.full_name AS reporter_name,
               u.study_group AS reporter_group, u.role AS reporter_role,
               (SELECT group_concat(c.title, ', ')
                  FROM report_categories rc JOIN categories c ON c.code = rc.category_code
                 WHERE rc.report_id = r.id) AS category_list
        FROM reports r
        JOIN devices d ON d.code = r.device_code
        JOIN users u ON u.id = r.reporter_id
        {where}
        ORDER BY r.created_at DESC, r.id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return rows
