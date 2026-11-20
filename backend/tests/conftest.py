from pathlib import Path
from secrets import token_urlsafe

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.cli import bootstrap_coach
from app.config import Settings
from app.main import create_app

ORIGIN = "https://testserver"
COACH_EMAIL = "coach@example.com"


@pytest.fixture
def migration(tmp_path, monkeypatch):
    path = tmp_path / "tests.sqlite3"
    monkeypatch.setenv("CTM_DATABASE_PATH", str(path))
    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    return path, config


@pytest.fixture
def password():
    return token_urlsafe(24)


@pytest.fixture
def client(migration, password):
    path, config = migration
    command.upgrade(config, "head")
    settings = Settings(database_path=path, allowed_origins=[ORIGIN], secure_cookies=True, serve_frontend=False)
    bootstrap_coach(settings, COACH_EMAIL, "Главный тренер", password)
    with TestClient(create_app(settings), base_url=ORIGIN) as session:
        yield session
