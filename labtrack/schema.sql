-- LabTrack: схема базы данных (SQLite)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash  TEXT NOT NULL,
    full_name      TEXT NOT NULL,
    study_group    TEXT NOT NULL DEFAULT '',
    role           TEXT NOT NULL CHECK (role IN ('student', 'lab')),
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    last_login_at  TEXT
);

CREATE TABLE IF NOT EXISTS rooms (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    number  TEXT NOT NULL UNIQUE,
    title   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    code          TEXT PRIMARY KEY,                 -- 'pc-15', часть адреса в QR-коде
    room_id       INTEGER NOT NULL REFERENCES rooms(id),
    type          TEXT NOT NULL CHECK (type IN ('pc', 'printer', 'projector')),
    name          TEXT NOT NULL,                    -- 'ПК-15'
    inventory_no  TEXT NOT NULL UNIQUE,             -- 'ИНВ-52-37-015'
    place         TEXT NOT NULL DEFAULT '',
    sort_order    INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS categories (
    code        TEXT PRIMARY KEY,                   -- 'mouse'
    type        TEXT,                               -- NULL = для всех типов
    title       TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    device_code  TEXT NOT NULL REFERENCES devices(code) ON UPDATE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('ok', 'problem', 'service')),
    comment      TEXT NOT NULL DEFAULT '',
    reporter_id  INTEGER NOT NULL REFERENCES users(id),
    created_at   TEXT NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0,
    resolved_at  TEXT,
    resolved_by  INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_reports_device_time ON reports (device_code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_open ON reports (device_code) WHERE kind = 'problem' AND resolved = 0;

CREATE TABLE IF NOT EXISTS report_categories (
    report_id      INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    category_code  TEXT NOT NULL REFERENCES categories(code),
    PRIMARY KEY (report_id, category_code)
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip          TEXT NOT NULL,
    username    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts (ip, created_at);

INSERT OR IGNORE INTO categories (code, type, title, sort_order) VALUES
  ('mouse','pc','Мышь',1), ('keyboard','pc','Клавиатура',2), ('monitor','pc','Монитор',3),
  ('software','pc','ПО / программы',4), ('system','pc','Не включается',5), ('network','pc','Нет интернета',6),
  ('noprint','printer','Не печатает',1), ('jam','printer','Замятие бумаги',2),
  ('paper','printer','Нет бумаги',3), ('toner','printer','Картридж / тонер',4),
  ('image','projector','Нет изображения',1), ('remote','projector','Пульт',2),
  ('cable','projector','Кабель / HDMI',3), ('lamp','projector','Тусклая картинка',4),
  ('other', NULL, 'Другое',99);
