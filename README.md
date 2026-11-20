# Cycling Team Monitor

Закрытое приложение для спортсменов и тренеров велокоманды. Спортсмены ведут утренний дневник, загружают FIT и отмечают RPE. Тренеры видят команду, историю и нагрузку по фактическим данным тренировок. Статус готовности к испытаниям приведён в [отчёте](docs/RELEASE_READINESS.md).

## Установка на Windows

Установите **PowerShell 7**, **Python 3.13** доступный как `python` и **Node.js 24** с npm. Откройте PowerShell 7 в корне проекта. Первая установка требует доступа к npm и PyPI. Если другая версия Python стоит первой в PATH, передайте путь к Python 3.13 через `setup.ps1 -PythonCommand`.

```powershell
.\scripts\setup.ps1
.\scripts\migrate.ps1
Push-Location backend
..\.venv\Scripts\python.exe -m app.cli bootstrap-coach --email coach@example.com --name "Имя тренера"
Pop-Location
.\scripts\server.ps1 start
```

Замените пример email на адрес тренера. CLI один раз создаёт первого тренера и запрашивает пароль скрытым вводом. Остальных тренеров и спортсменов приглашает тренер в приложении. Скрипт установки сохраняет существующий `.env`, использует зафиксированные версии зависимостей и строит PWA. Миграции запускаются отдельной командой.

Откройте [локальное приложение](http://127.0.0.1:8000). По умолчанию SQLite и FIT лежат в `data`. Локальный HTTP подходит для работы на этом ноутбуке. Для LAN и удалённого входа настройте HTTPS до приглашения участников. [Запуск, остановка, локальная сеть и HTTPS](docs/WINDOWS_DEPLOYMENT.md)

## Ежедневная работа

```powershell
.\scripts\server.ps1 start
.\scripts\server.ps1 status
.\scripts\server.ps1 stop
```

Пока сервер выключен или ноутбук спит, браузер и фоновая синхронизация не могут обращаться к приложению. Записанные данные сохраняются на диске. [Резервное копирование и восстановление](docs/BACKUP_RESTORE.md)

- [Руководство тренера](docs/COACH_GUIDE.md) и [методика аналитики](docs/COACH_ANALYTICS.md)
- [Руководство спортсмена](docs/ATHLETE_GUIDE.md) и [подключение устройств, установка PWA](docs/DEVICE_SETUP.md)
- [Импорт FIT и методика расчётов](docs/FIT_IMPORT.md)
- [Испытания команды и создание отдельной тестовой базы](docs/TEAM_PILOT.md)
- [Адаптер OnelapFit и проверка реального аккаунта](docs/ONELAPFIT_VALIDATION.md). Автосинхронизация требует разрешённого HTTPS-шлюза. Пока используйте ручную загрузку FIT.
- [Готовность, известные ограничения и соответствие ТЗ](docs/RELEASE_READINESS.md)

## Разработка и проверки

Установите тестовые зависимости через `.\scripts\setup.ps1 -Dev`. Для разработки запустите в разных окнах Uvicorn и Vite.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --reload --reload-dir backend
npm.cmd --prefix frontend run dev
```

Интерфейс разработки доступен на [localhost:5173](http://localhost:5173), схема API — на [localhost:8000/api/v1/docs](http://localhost:8000/api/v1/docs). Для production Vite собирает статику, которую раздаёт FastAPI на том же origin.

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\scripts\migrate.ps1 -Check
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
Push-Location frontend
npx.cmd playwright install chromium
npm.cmd run test:e2e
Pop-Location
```

`-Check` проверяет, что настроенная база уже на последней миграции и соответствует моделям. Pytest и Playwright создают отдельные базы данных. Playwright запускает production-сборку в Chromium с размерами экранов Windows, Android и iPhone.
