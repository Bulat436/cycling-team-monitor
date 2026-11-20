"""Run the built application on a temporary SQLite database for Playwright."""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.cli import bootstrap_coach
from app.config import Settings
from app.main import create_app


def main() -> None:
    origin = os.environ["CTM_E2E_ORIGIN"]
    address = urlsplit(origin)
    directory = os.environ["CTM_E2E_DIRECTORY"]
    database = Path(directory) / "test.sqlite3"
    os.environ["CTM_DATABASE_PATH"] = str(database)
    command.upgrade(Config(BACKEND / "alembic.ini"), "head")
    settings = Settings(
        database_path=database, allowed_origins=[origin], serve_frontend=True, secure_cookies=False,
    )
    bootstrap_coach(settings, "coach@example.com", "Главный тренер", os.environ["CTM_E2E_PASSWORD"])
    uvicorn.run(create_app(settings), host=address.hostname, port=address.port)


if __name__ == "__main__":
    main()
