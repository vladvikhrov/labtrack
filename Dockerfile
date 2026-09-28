FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/labtrack.sqlite3 \
    FLASK_APP=labtrack

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY labtrack ./labtrack
COPY wsgi.py .

RUN useradd --system --uid 10001 app && mkdir -p /data && chown app /data
USER app
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["gunicorn", "--workers", "2", "--threads", "4", "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", "wsgi:app"]
