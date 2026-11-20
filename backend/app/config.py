from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CTM_", env_file=ROOT / ".env", extra="ignore"
    )

    database_path: Path = Path("data/team.sqlite3")
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    secure_cookies: bool = True
    serve_frontend: bool = False
    session_hours: int = Field(default=24, ge=1, le=168)
    invitation_hours: int = Field(default=72, ge=1, le=720)
    reset_hours: int = Field(default=1, ge=1, le=24)

    @property
    def database_file(self) -> Path:
        return ROOT / self.database_path

    @property
    def cookie_name(self) -> str:
        return "__Host-ctm_session" if self.secure_cookies else "ctm_session"
