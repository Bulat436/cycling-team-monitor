# AGENTS.md — карта Cycling Team Monitor

## С чего начать

- [docs/PRD.md](docs/PRD.md) — требования к продукту. [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) — этапы A–F и принятые решения.
- [README.md](README.md) — установка, запуск и команды проверки. [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md) — результаты и открытые проверки на базе команды.
- Браузер работает через React/Vite и REST API `/api/v1`. FastAPI обращается к SQLite через SQLAlchemy. В разработке Vite проксирует API, в собранной версии FastAPI раздаёт интерфейс и API с одного адреса.

## Где менять код

| Задача | Сервер в `backend/app/` | Интерфейс в `frontend/src/` |
| --- | --- | --- |
| Точки входа, маршруты и конфигурация | `main.py` — фабрика FastAPI, регистрация маршрутов и запуск планировщика. `config.py` — настройки `CTM_*`. `database.py` — подключения и транзакции SQLite. | `main.tsx` — запуск React. `App.tsx` — маршруты и интерфейсы ролей. `api.ts` и `session.ts` — запросы, ошибки и состояние входа. |
| Вход, сессии, приглашения, аудит | `auth.py`, `security.py`, `rate_limit.py`, `admin.py`, `models.py`. `cli.py` создаёт первого тренера и сбрасывает пароль. | `SignIn.tsx`, `Coach.tsx`. |
| Профиль, масса, FTP, зоны и дневник | `athletes.py`, `wellness.py`, `athlete_models.py`, `athlete_schemas.py`. | `AthleteHome.tsx`, `AthleteProfile.tsx`, `Wellness.tsx`, `athleteApi.ts`. |
| Импорт FIT, показатели, временные ряды и RPE | `workout_fit_routes.py` принимает загрузку. `workout_import.py` — общий сервис импорта. `fit_parser.py` читает FIT, `workout_metrics.py` считает показатели. `workouts.py` и `workout_series.py` отдают данные, `workout_models.py` описывает хранение. | `Workouts.tsx`, `WorkoutDetail.tsx`, `WorkoutCharts.tsx`. |
| Панель тренера, аналитика и предупреждения | `analytics.py`, `analytics_schemas.py`, `analytics_models.py`, `alert_rules.py`. | `Team.tsx`, `CoachAthlete.tsx`, `CoachAnalytics.tsx`, `AlertSettings.tsx`, `PeriodPicker.tsx`, `Charts.tsx`, `coachApi.ts`. |
| Синхронизация OnelapFit | `onelap_routes.py` — API. `onelap_provider.py` — клиент шлюза, `onelap_secrets.py` — шифрование, `onelap_scheduler.py` — планировщик, `onelap_sync.py` — синхронизация через общий сервис импорта FIT. | `OnelapFit.tsx`. |
| PWA и сборка | `main.py` раздаёт `frontend/dist/`, включая manifest и service worker. | `Pwa.tsx` управляет установкой и обновлением. Manifest и иконки в `frontend/public/`, генерация worker в `frontend/scripts/`. |
| Windows и резервные копии | `windows_server.py` — локальный сервер и штатная остановка. `backup.py` — создание, проверка и восстановление архива. | В корне `Start.cmd` и `Stop.cmd`. В `scripts/` — установка, миграции, запуск и проверка состояния. |

## Данные и ограничения, важные при изменениях

- Схема меняется через Alembic в `backend/alembic/versions/`. `backend/alembic/env.py` загружает модели и включает SQLite WAL.
- Рабочая база и оригиналы FIT по умолчанию находятся в `data/team.sqlite3` и `data/fit/`. Пути переопределяются в `.env`, шаблон настроек — `.env.example`. Рабочие данные, архивы и секреты держите вне Git и публичных каталогов.
- Роль и принадлежность данных проверяет сервер. При работе с изменяющими маршрутами учитывайте проверки Origin, сессии и CSRF. Для расчётных показателей используйте сохранённые параметры, в том числе исторический FTP.
- Ручной FIT и синхронизация проходят через `WorkoutImportService`. Настоящий аккаунт OnelapFit требует разрешённого HTTPS-шлюза и отдельной проверки. [Методика FIT](docs/FIT_IMPORT.md) · [Проверка OnelapFit](docs/ONELAPFIT_VALIDATION.md)

## Как проверить изменения

Выполняйте команды в PowerShell 7 из корня проекта.

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\scripts\migrate.ps1 -Check
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run test:e2e
```

- `backend/tests/` — pytest, миграции, контрольные FIT и фикстуры шлюза. `backend/tests/seed_team.py` создаёт отдельную тестовую команду на 20 спортсменов и 3 тренеров.
- `frontend/src/*.test.tsx` — Vitest. `frontend/e2e/` — Playwright с production-сборкой и временной БД. Конфигурация в `frontend/playwright.config.ts`, запуск тестового API в `backend/tests/run_e2e.py`.
- [Простая инструкция](docs/QUICK_START_RU.txt) · [Windows и HTTPS](docs/WINDOWS_DEPLOYMENT.md) · [Резервное копирование](docs/BACKUP_RESTORE.md) · [Чек-лист пилота](docs/TEAM_PILOT.md)
