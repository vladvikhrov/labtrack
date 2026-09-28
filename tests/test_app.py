"""Автотесты LabTrack: python -m unittest discover -s tests -v"""
import os
import re
import tempfile
import unittest

from labtrack import create_app
from labtrack.auth import create_user
from labtrack.db import get_db


class LabTrackTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test",
            "DATABASE": os.path.join(self.tmp.name, "t.sqlite3"),
            "REGISTRATION_CODE": "AUD214",
        })
        runner = self.app.test_cli_runner()
        res = runner.invoke(args=["seed"])
        self.assertIn("добавлено устройств — 33", res.output)
        with self.app.app_context():
            create_user("lab", "labpass123", "Смирнова Е. В.", "lab")
            create_user("ivanov", "stud12345", "Иванов Пётр", "student", "ИС-21")
            create_user("petrova", "stud12345", "Петрова Анна", "student", "ИС-22")
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    # --- помощники ---
    def csrf(self, url="/auth/login"):
        html = self.client.get(url).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def login(self, username, password, next_url=None):
        data = {"username": username, "password": password, "csrf_token": self.csrf()}
        if next_url:
            data["next"] = next_url
        return self.client.post("/auth/login", data=data)

    def post(self, url, data, csrf_from=None):
        data = dict(data, csrf_token=self.csrf(csrf_from or url))
        return self.client.post(url, data=data)

    def count(self, sql, *params):
        with self.app.app_context():
            return get_db().execute(sql, params).fetchone()[0]

    # --- авторизация ---
    def test_qr_link_requires_login_and_returns_back(self):
        res = self.client.get("/d/pc-15")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/auth/login?next=/d/pc-15", res.headers["Location"])
        res = self.login("ivanov", "stud12345", "/d/pc-15")
        self.assertEqual(res.headers["Location"], "/d/pc-15")
        self.assertIn("ПК-15", self.client.get("/d/pc-15").get_data(as_text=True))

    def test_wrong_password_and_lockout(self):
        for _ in range(5):
            res = self.login("ivanov", "wrong")
            self.assertIn("Неверный логин или пароль", res.get_data(as_text=True))
        res = self.login("ivanov", "stud12345")
        self.assertIn("Слишком много неудачных попыток", res.get_data(as_text=True))

    def test_open_redirect_blocked(self):
        res = self.login("ivanov", "stud12345", "https://evil.example/")
        self.assertEqual(res.headers["Location"], "/")

    def test_post_without_csrf_rejected(self):
        self.login("ivanov", "stud12345")
        res = self.client.post("/d/pc-1", data={"action": "ok"})
        self.assertEqual(res.status_code, 400)

    def test_student_cannot_open_lab(self):
        self.login("ivanov", "stud12345")
        for url in ("/lab/", "/lab/device/pc-1", "/lab/stickers", "/lab/users", "/lab/devices"):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_blocked_user_logged_out(self):
        self.login("ivanov", "stud12345")
        self.assertEqual(self.client.get("/me").status_code, 200)
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE users SET is_active = 0 WHERE username = 'ivanov'")
            db.commit()
        self.assertEqual(self.client.get("/me").status_code, 302)

    def test_register_requires_code(self):
        form = {"full_name": "Ким Алексей", "study_group": "ПИ-23", "username": "kim",
                "password": "secret123", "password2": "secret123"}
        res = self.post("/auth/register", dict(form, invite="WRONG"))
        self.assertIn("Неверный код регистрации", res.get_data(as_text=True))
        res = self.post("/auth/register", dict(form, invite="aud214"))
        self.assertEqual(res.status_code, 302)
        self.assertEqual(self.count("SELECT COUNT(*) FROM users WHERE username='kim' AND role='student'"), 1)

    def test_register_closed_without_code(self):
        self.app.config["REGISTRATION_CODE"] = ""
        self.assertEqual(self.client.get("/auth/register").status_code, 404)

    # --- сценарий кейса ---
    def test_full_case_flow(self):
        # трое студентов жалуются на мышь ПК-15
        for user in ("ivanov", "petrova"):
            self.client = self.app.test_client()
            self.login(user, "stud12345")
            res = self.post("/d/pc-15", {"action": "problem", "categories": "mouse", "comment": "Не крутится колёсико"},
                            csrf_from="/d/pc-15?step=problem")
            self.assertIn("step=sent-problem", res.headers["Location"])
        # повторная жалоба того же студента в течение 10 минут отклоняется
        res = self.post("/d/pc-15", {"action": "problem", "categories": "mouse"}, csrf_from="/d/pc-15?step=problem")
        self.assertIn("уже сообщали", res.get_data(as_text=True))
        # «Всё работает» на другом ПК
        self.post("/d/pc-3", {"action": "ok"})
        # без категории — ошибка
        res = self.post("/d/pc-7", {"action": "problem"}, csrf_from="/d/pc-7?step=problem")
        self.assertIn("Выберите хотя бы один вариант", res.get_data(as_text=True))

        # лаборант видит дашборд
        self.client = self.app.test_client()
        self.login("lab", "labpass123")
        html = self.client.get("/lab/").get_data(as_text=True)
        self.assertIn("мышь ×2", html)
        self.assertIn("Петрова Анна, ИС-22", html)
        # история и закрытие
        html = self.client.get("/lab/device/pc-15").get_data(as_text=True)
        self.assertIn("Иванов Пётр, ИС-21", html)
        self.post("/lab/device/pc-15/resolve", {"note": "Заменена мышь"}, csrf_from="/lab/device/pc-15")
        self.assertEqual(self.count(
            "SELECT COUNT(*) FROM reports WHERE device_code='pc-15' AND kind='problem' AND resolved=0"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM reports WHERE kind='service'"), 1)
        self.assertIn("Жалоб нет", self.client.get("/lab/").get_data(as_text=True))

    def test_invalid_category_ignored(self):
        self.login("ivanov", "stud12345")
        self.post("/d/printer-1", {"action": "problem", "categories": "mouse"}, csrf_from="/d/printer-1?step=problem")
        self.assertEqual(self.count("SELECT COUNT(*) FROM reports"), 0)

    def test_unknown_device_404(self):
        self.login("ivanov", "stud12345")
        self.assertEqual(self.client.get("/d/pc-99").status_code, 404)

    def test_stickers_have_qr_for_every_device(self):
        self.login("lab", "labpass123")
        html = self.client.get("/lab/stickers").get_data(as_text=True)
        self.assertEqual(html.count("<svg"), 33)
        self.post("/lab/stickers", {"base_url": "https://labtrack.example.ru"})
        html = self.client.get("/lab/stickers").get_data(as_text=True)
        self.assertIn("https://labtrack.example.ru/", html)

    def test_lab_creates_user_and_resets_password(self):
        self.login("lab", "labpass123")
        res = self.post("/lab/users", {"action": "create", "full_name": "Орлова Мария",
                                       "study_group": "ИС-21", "username": "orlova", "role": "student"})
        pwd = re.search(r"<code>([a-z0-9]{10})</code>", res.get_data(as_text=True)).group(1)
        self.client = self.app.test_client()
        self.assertEqual(self.login("orlova", pwd).status_code, 302)

    def test_password_change_invalidates_other_sessions(self):
        other = self.app.test_client()
        self.login("ivanov", "stud12345")
        self.client, main = other, self.client
        self.login("ivanov", "stud12345")
        self.client = main
        self.post("/auth/password", {"old": "stud12345", "new": "newpass123", "new2": "newpass123"})
        self.assertEqual(other.get("/me").status_code, 302)   # старая сессия больше не действует
        self.assertEqual(self.client.get("/me").status_code, 200)

    def test_lab_adds_and_edits_device(self):
        self.login("lab", "labpass123")
        self.post("/lab/devices", {"code": "pc-31", "name": "ПК-31", "inventory_no": "ИНВ-214-031", "place": "Ряд 6"})
        self.assertEqual(self.count("SELECT COUNT(*) FROM devices"), 34)
        self.post("/lab/device/pc-31/edit", {"name": "ПК-31", "inventory_no": "ИНВ-214-031", "place": ""},
                  csrf_from="/lab/device/pc-31")
        self.assertEqual(self.count("SELECT is_active FROM devices WHERE code='pc-31'"), 0)

    def test_healthz(self):
        self.assertEqual(self.client.get("/healthz").get_json(), {"status": "ok"})


class QrTestCase(unittest.TestCase):
    def test_qr_sizes(self):
        from labtrack.qr import encode
        self.assertEqual(len(encode("https://labtrack.example.ru/d/pc-15")), 29)  # версия 3
        with self.assertRaises(ValueError):
            encode("x" * 300)


if __name__ == "__main__":
    unittest.main()
