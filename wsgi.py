"""Точка входа для gunicorn: gunicorn wsgi:app"""
import os

from labtrack import create_app

if not os.environ.get("SECRET_KEY"):
    raise RuntimeError("Задайте переменную окружения SECRET_KEY (см. .env.example).")

app = create_app()
