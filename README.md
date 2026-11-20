# Cycling Team Monitor

Закрытое приложение для спортсменов и тренеров велокоманды. Этап A включает локальный запуск и закрытое управление доступом. Этап B добавляет профили, историю измерений, дневники и RPE. Следующие этапы и критерии приёмки описаны в [плане реализации](docs/IMPLEMENTATION_PLAN.md).

## Установка и запуск на Windows

Понадобятся Python 3.12+ и Node.js 22.12+ с npm. Выполняйте PowerShell-команды из корня репозитория.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
Copy-Item .env.example .env
npm.cmd --prefix frontend ci
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head
Push-Location backend
..\.venv\Scripts\python.exe -m app.cli bootstrap-coach --email coach@example.com --name "Имя тренера"
Pop-Location
```

При повторной установке сохраните действующий `.env`. Команда инициализации запрашивает пароль без отображения и создаёт первого тренера один раз. Укажите адрес тренера и уникальный пароль длиной от 12 символов. По умолчанию БД создаётся в `data/team.sqlite3`.

Для разработки откройте два окна PowerShell в корне проекта.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --reload --reload-dir backend
```

```powershell
npm.cmd --prefix frontend run dev
```

Интерфейс доступен на [localhost:5173](http://localhost:5173). Vite проксирует `/api` в FastAPI. Состояние сервера доступно по [localhost:8000/api/v1/health](http://localhost:8000/api/v1/health), схемы API по [localhost:8000/api/v1/docs](http://localhost:8000/api/v1/docs).

## Запуск собранного приложения

```powershell
npm.cmd --prefix frontend run build
$env:CTM_SERVE_FRONTEND = 'true'
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
```

Откройте [localhost:8000](http://localhost:8000). При изменении схемы применяйте миграции отдельной командой до запуска сервера.

Для доступа с других устройств подключите HTTPS-прокси с проверенным устройствами сертификатом к FastAPI на loopback. В `.env` включите `CTM_SECURE_COOKIES=true` и задайте `CTM_ALLOWED_ORIGINS` как JSON-массив точных HTTPS-адресов приложения без завершающего слеша. Укажите адрес публичного входа в интерфейс; выданные приглашения будут использовать этот origin. Доверяйте прокси-заголовкам только от адреса вашего прокси. Значение `CTM_SECURE_COOKIES=false` в примере предназначено для локальной разработки по HTTP. Реальный удалённый доступ проверяется на этапе F.

## Управление доступом

Первый тренер может пригласить спортсменов и других тренеров в разделе «Управление доступом». Передайте одноразовую ссылку выбранному получателю. По умолчанию приглашение действует 72 часа, ссылка сброса — 1 час, сессия — 24 часа. Ссылку можно отозвать или выпустить заново.

Административный сброс сразу отключает действующий пароль и завершает сессии выбранного пользователя. Деактивация закрывает доступ и отзывает ссылки. Тренер может запросить сброс у другого тренера. Администратор Windows-сервера может восстановить пароль активного тренера локально командой `..\.venv\Scripts\python.exe -m app.cli reset-coach-password --email coach@example.com` из каталога `backend`. Команда завершает предыдущие сессии и отзывает старые ссылки доступа.

SQLite хранит хеши Argon2id для паролей и SHA-256 для высокоэнтропийных секретов сессий и ссылок. Вход ограничен пятью попытками на сочетание адреса клиента и учётной записи и 50 попытками на адрес клиента за 15 минут. Срок и счётчики сохраняются в БД. Для изменяющих запросов сервер проверяет Origin, для авторизованных действий дополнительно требуется `X-CSRF-Token` из ответа `/api/v1/auth/me` или `/api/v1/auth/login`. База и `.env` исключены из Git.

## Профили, дневники и тренировки

Тренер открывает профиль из списка команды и меняет данные, часовой пояс, FTP, массу тела и зоны. Спортсмен видит свой профиль и измерения. Заметки тренера доступны тренерам.

Дневник использует календарную дату спортсмена. По умолчанию часовой пояс — `Europe/Moscow`. Укажите нужную зону IANA в профиле. Изменение часового пояса действует на расчёт текущей даты. Сохранённые анкеты сохраняют исходные дату и пояс. Можно заполнить прошедшую дату или исправить анкету. Тренер видит состояние команды за местную дату каждого спортсмена и историю анкет.

RPE доступен для каждой существующей тренировки. Список тренировок заполняется после появления импорта FIT на этапе C. Подключение OnelapFit, графики, предупреждения и установка PWA относятся к следующим этапам.

[Контракт данных этапа B](docs/IMPLEMENTATION_PLAN.md#решения-этапа-b)

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini check
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
Push-Location frontend
npx.cmd playwright install chromium
npm.cmd run test:e2e
Pop-Location
```

Команда `alembic check` проверяет локальную БД после применения миграции. Playwright запускает собранное приложение на отдельном порту с временной БД и сгенерированным паролем. Chromium проверяет интерфейс в размерах экрана Windows, Android и iPhone.
