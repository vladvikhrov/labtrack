"""Команды администрирования: flask --app labtrack <команда>."""
import csv
from datetime import datetime, timedelta, timezone

import click
from flask import current_app
from flask.cli import with_appcontext

from .auth import USERNAME_RE, create_user, generate_temp_password, set_password
from .db import get_db, init_db


def register(app):
    app.cli.add_command(init_db_cmd)
    app.cli.add_command(seed_cmd)
    app.cli.add_command(create_user_cmd)
    app.cli.add_command(reset_password_cmd)
    app.cli.add_command(import_students_cmd)


@click.command("init-db")
@with_appcontext
def init_db_cmd():
    """Создать таблицы (безопасно запускать повторно)."""
    init_db()
    click.echo("База данных готова.")


@click.command("seed")
@with_appcontext
@click.option("--pcs", default=30, show_default=True, help="Сколько ПК в классе")
@click.option("--demo", is_flag=True, help="Добавить демо-пользователей и примерные сообщения")
def seed_cmd(pcs, demo):
    """Создать аудиторию и устройства: ПК-1…ПК-N, 2 принтера, проектор."""
    db = get_db()
    room_no = current_app.config["ROOM_NUMBER"]
    db.execute("INSERT OR IGNORE INTO rooms (number, title) VALUES (?, 'Компьютерный класс')", (room_no,))
    room_id = db.execute("SELECT id FROM rooms WHERE number = ?", (room_no,)).fetchone()[0]
    rows = [(f"pc-{i}", room_id, "pc", f"ПК-{i}", f"ИНВ-{room_no}-{i:03d}",
             f"Ряд {(i - 1) // 6 + 1}, место {(i - 1) % 6 + 1}", i) for i in range(1, pcs + 1)]
    rows += [
        ("printer-1", room_id, "printer", "Принтер-1", f"ИНВ-{room_no}-101", "У окна", 1),
        ("printer-2", room_id, "printer", "Принтер-2", f"ИНВ-{room_no}-102", "У двери", 2),
        ("projector-1", room_id, "projector", "Проектор", f"ИНВ-{room_no}-201", "Потолок, у доски", 1),
    ]
    before = db.total_changes
    db.executemany(
        "INSERT OR IGNORE INTO devices (code, room_id, type, name, inventory_no, place, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    db.commit()
    click.echo(f"Аудитория {room_no}: добавлено устройств — {db.total_changes - before} (из {len(rows)}).")
    if demo:
        _seed_demo(db)


def _seed_demo(db):
    users = [("lab", "lab12345", "Лаборант (демо)", "", "lab"),
             ("ivanov", "student123", "Иванов Пётр", "ИС-21", "student"),
             ("petrova", "student123", "Петрова Анна", "ИС-22", "student"),
             ("kim", "student123", "Ким Алексей", "ПИ-23", "student")]
    ids = {}
    for u, p, n, grp, role in users:
        row = db.execute("SELECT id FROM users WHERE username = ?", (u,)).fetchone()
        ids[u] = row[0] if row else create_user(u, p, n, role, grp)
    if db.execute("SELECT COUNT(*) FROM reports").fetchone()[0]:
        click.echo("Сообщения уже есть — демо-сообщения не добавлены.")
        return
    now = datetime.now(timezone.utc).replace(microsecond=0)
    at = lambda h: (now - timedelta(hours=h)).isoformat()
    demo = [("pc-15", "problem", ["mouse"], "Колёсико не крутится", "ivanov", 2),
            ("pc-15", "problem", ["mouse"], "", "petrova", 5),
            ("pc-15", "problem", ["mouse"], "Курсор дёргается", "kim", 26),
            ("pc-7", "problem", ["monitor"], "Мерцает экран", "ivanov", 8),
            ("printer-1", "problem", ["jam"], "", "petrova", 3),
            ("pc-22", "problem", ["software", "network"], "Не открывается браузер", "kim", 30),
            ("pc-3", "ok", [], "", "ivanov", 1),
            ("projector-1", "ok", [], "", "petrova", 4)]
    for dev, kind, cats, comment, who, h in demo:
        cur = db.execute("INSERT INTO reports (device_code, kind, comment, reporter_id, created_at) "
                         "VALUES (?, ?, ?, ?, ?)", (dev, kind, comment, ids[who], at(h)))
        db.executemany("INSERT INTO report_categories VALUES (?, ?)", [(cur.lastrowid, c) for c in cats])
    cur = db.execute("INSERT INTO reports (device_code, kind, comment, reporter_id, created_at, resolved, "
                     "resolved_at, resolved_by) VALUES ('pc-11','problem','Залипает пробел',?,?,1,?,?)",
                     (ids["kim"], at(72), at(50), ids["lab"]))
    db.execute("INSERT INTO report_categories VALUES (?, 'keyboard')", (cur.lastrowid,))
    db.execute("INSERT INTO reports (device_code, kind, comment, reporter_id, created_at, resolved, resolved_at, "
               "resolved_by) VALUES ('pc-11','service','Заменена клавиатура',?,?,1,?,?)",
               (ids["lab"], at(50), at(50), ids["lab"]))
    db.commit()
    click.echo("Демо: пользователи lab / lab12345 и ivanov, petrova, kim / student123; 10 сообщений.")


@click.command("create-user")
@with_appcontext
@click.argument("username")
@click.option("--name", "full_name", required=True, help="ФИО")
@click.option("--role", type=click.Choice(["lab", "student"]), default="lab", show_default=True)
@click.option("--group", "study_group", default="", help="Учебная группа (для студента)")
@click.password_option(help="Пароль (не короче 8 символов)")
def create_user_cmd(username, full_name, role, study_group, password):
    """Создать пользователя. Пример: flask create-user lab --name "Смирнова Е. В." """
    if not USERNAME_RE.match(username):
        raise click.BadParameter("логин: 3–32 символа, латиница, цифры, . _ -")
    if len(password) < 8:
        raise click.BadParameter("пароль короче 8 символов")
    if get_db().execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        raise click.ClickException(f"Пользователь {username} уже существует.")
    create_user(username, password, full_name, role, study_group)
    click.echo(f"Создан пользователь {username} ({role}).")


@click.command("reset-password")
@with_appcontext
@click.argument("username")
def reset_password_cmd(username):
    """Выдать пользователю новый временный пароль."""
    row = get_db().execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        raise click.ClickException("Пользователь не найден.")
    pwd = generate_temp_password()
    set_password(row["id"], pwd)
    click.echo(f"Новый пароль для {username}: {pwd}")


@click.command("import-students")
@with_appcontext
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
def import_students_cmd(csv_path):
    """Импорт студентов из CSV (колонки: username;full_name;study_group). Выводит пароли."""
    db = get_db()
    created = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        reader = csv.DictReader(f, dialect=csv.Sniffer().sniff(sample, delimiters=";,"))
        click.echo("username;full_name;study_group;password")
        for row in reader:
            u = (row.get("username") or "").strip()
            if not USERNAME_RE.match(u) or db.execute("SELECT 1 FROM users WHERE username = ?", (u,)).fetchone():
                click.echo(f"# пропущен: {u or '(пусто)'}", err=True)
                continue
            pwd = generate_temp_password()
            create_user(u, pwd, row.get("full_name", u).strip(), "student", (row.get("study_group") or "").strip())
            click.echo(f"{u};{row.get('full_name', '').strip()};{(row.get('study_group') or '').strip()};{pwd}")
            created += 1
    click.echo(f"# создано: {created}", err=True)
