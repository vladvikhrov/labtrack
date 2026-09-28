#!/bin/sh
# Резервная копия базы LabTrack. Запускать из папки проекта на сервере.
# Для ежедневного запуска: crontab -e →  0 3 * * * cd /opt/labtrack && ./deploy/backup.sh
set -e
mkdir -p backups
STAMP=$(date +%Y-%m-%d_%H-%M)
docker compose exec -T web python -c "
import sqlite3, sys
src = sqlite3.connect('/data/labtrack.sqlite3')
dst = sqlite3.connect('/data/backup.sqlite3')
src.backup(dst); dst.close(); src.close()"
docker compose cp web:/data/backup.sqlite3 "backups/labtrack_$STAMP.sqlite3"
# хранить копии за последние 30 дней
find backups -name 'labtrack_*.sqlite3' -mtime +30 -delete
echo "Готово: backups/labtrack_$STAMP.sqlite3"
