# Содержимое WSGI-файла на PythonAnywhere (вкладка Web → WSGI configuration file).
# Замените USERNAME на своё имя пользователя и заполните значения.
import os
import sys

PROJECT = "/home/USERNAME/labtrack"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

os.environ["SECRET_KEY"] = "вставьте-длинную-случайную-строку"
os.environ["DATABASE_PATH"] = PROJECT + "/data/labtrack.sqlite3"
os.environ["REGISTRATION_CODE"] = "CODE123"  # замените на свой код
os.environ["ROOM_NUMBER"] = "52-37"
os.environ["PUBLIC_BASE_URL"] = "https://USERNAME.pythonanywhere.com/"
os.environ["SESSION_COOKIE_SECURE"] = "1"
os.environ["TIMEZONE"] = "Europe/Moscow"

from wsgi import app as application  # noqa: E402
