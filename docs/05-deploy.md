# Развёртывание LabTrack

Есть два способа. Выберите один.

| | Вариант А: свой сервер (VPS) + Docker | Вариант Б: PythonAnywhere |
|---|---|---|
| Для чего | Постоянная работа в колледже | Демонстрация, пилот |
| Нужно | VPS с Ubuntu, домен | Только аккаунт на pythonanywhere.com |
| Адрес | `https://labtrack.ваш-домен.ru` | `https://ИМЯ.pythonanywhere.com` |
| HTTPS | Автоматически (Caddy + Let's Encrypt) | Включён на стороне хостинга |
| Время настройки | ≈ 30 минут | ≈ 20 минут |

> Адрес сайта попадает в QR-коды. Решите, какой адрес будет постоянным, **до печати наклеек**: после смены адреса наклейки придётся перепечатать.

---

## Вариант А. Свой сервер с Docker

### A.1. Что понадобится

- VPS с Ubuntu 22.04/24.04, 1 ГБ памяти и публичным IP. Подойдёт любой хостинг или сервер колледжа.
- Домен или поддомен, например `labtrack.college.ru`. В DNS создайте **A-запись** на IP сервера.
- Открытые порты 80 и 443.

### A.2. Установка Docker (один раз)

```bash
ssh root@IP-сервера
curl -fsSL https://get.docker.com | sh
```

### A.3. Загрузка проекта и настройка

```bash
git clone https://github.com/ВАШ-АККАУНТ/labtrack.git /opt/labtrack
cd /opt/labtrack
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"   # скопируйте результат
nano .env
```

В `.env` заполните:

| Переменная | Что указать |
|---|---|
| `DOMAIN` | Домен без `https://`, например `labtrack.college.ru` |
| `SECRET_KEY` | Строку из команды выше |
| `REGISTRATION_CODE` | Код, по которому студенты регистрируются сами, например `CODE123`. Пусто — регистрирует только лаборант |
| `ROOM_NUMBER` | Номер аудитории |

### A.4. Запуск

```bash
docker compose up -d --build
docker compose exec web flask seed                     # аудитория и 33 устройства
docker compose exec web flask create-user lab --name "Смирнова Елена Викторовна"   # спросит пароль лаборанта
```

Откройте `https://ваш-домен` — должна открыться страница входа. Сертификат HTTPS Caddy получит сам в течение минуты.

### A.5. Обслуживание

| Задача | Команда |
|---|---|
| Посмотреть логи | `docker compose logs -f web` |
| Обновить после изменений в GitHub | `git pull && docker compose up -d --build` |
| Резервная копия базы | `./deploy/backup.sh` (копии в папке `backups/`) |
| Ежедневная копия в 03:00 | `crontab -e` → `0 3 * * * cd /opt/labtrack && ./deploy/backup.sh` |
| Новый пароль пользователю | `docker compose exec web flask reset-password ivanov` |
| Импорт списка студентов | `docker compose cp students.csv web:/tmp/s.csv && docker compose exec web flask import-students /tmp/s.csv` |

Формат `students.csv` (разделитель `;`, первая строка — заголовки):

```
username;full_name;study_group
ivanov.p;Иванов Пётр;ИС-21
petrova.a;Петрова Анна;ИС-22
```

Команда выведет список с временными паролями — раздайте их студентам.

---

## Вариант Б. PythonAnywhere (без своего сервера)

1. Зарегистрируйтесь на pythonanywhere.com (бесплатный тариф).
2. Откройте **Consoles → Bash** и выполните:
   ```bash
   git clone https://github.com/ВАШ-АККАУНТ/labtrack.git
   cd labtrack
   python3.12 -m venv ~/.venvs/labtrack
   source ~/.venvs/labtrack/bin/activate
   pip install -r requirements.txt
   mkdir -p data
   export DATABASE_PATH=~/labtrack/data/labtrack.sqlite3 SECRET_KEY=tmp
   flask --app labtrack seed
   flask --app labtrack create-user lab --name "Смирнова Е. В."
   ```
3. Вкладка **Web → Add a new web app → Manual configuration → Python 3.12**.
4. В поле **Virtualenv** укажите `/home/ИМЯ/.venvs/labtrack`.
5. Откройте **WSGI configuration file**, удалите всё и вставьте содержимое `deploy/pythonanywhere_wsgi.py`, заменив `USERNAME` и `SECRET_KEY`.
6. В разделе **Static files** добавьте: URL `/static/` → каталог `/home/ИМЯ/labtrack/labtrack/static`.
7. Включите **Force HTTPS** и нажмите **Reload**.

Бесплатное веб-приложение на PythonAnywhere нужно периодически продлевать кнопкой в панели Web, иначе оно отключится. Для постоянной работы в классе выберите вариант А.

---

## Проверка после развёртывания

- [ ] Сайт открывается по `https://`, браузер не ругается на сертификат.
- [ ] Лаборант входит и видит дашборд с 33 устройствами.
- [ ] Во вкладке «QR-наклейки» адрес сайта правильный.
- [ ] QR-код ПК-1, отсканированный телефоном, ведёт на вход, а после входа — на страницу ПК-1.
- [ ] Тестовая жалоба появляется на дашборде (обновление раз в 30 секунд), закрывается кнопкой «Отметить исправленным».
- [ ] Студент по адресу `/lab/` получает «Нет доступа».
- [ ] Резервная копия создаётся (`./deploy/backup.sh`).

## Безопасность

- Пароли хранятся только в виде хеша (scrypt, Werkzeug).
- Вход: 5 неудачных попыток с одного IP за 15 минут — блокировка на 15 минут.
- Все формы защищены CSRF-токеном; cookie сессии `HttpOnly`, `SameSite=Lax`, `Secure` при HTTPS.
- После смены пароля остальные сессии пользователя завершаются.
- Лаборант может заблокировать учётную запись; блокировка действует сразу.
- Самостоятельная регистрация возможна только с кодом регистрации; без кода её можно отключить полностью.
