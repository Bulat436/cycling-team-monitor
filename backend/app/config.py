from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


def private_storage_path(path: Path) -> Path:
    resolved = (ROOT / path).resolve()
    if resolved.is_relative_to((ROOT / "frontend").resolve()):
        raise ValueError("Application data must be outside the frontend directory")
    return resolved


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CTM_", env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )

    database_path: Path = Path("data/team.sqlite3")
    fit_path: Path = Path("data/fit")
    fit_max_bytes: int = Field(default=32 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    secure_cookies: bool = True
    serve_frontend: bool = False
    expose_api_docs: bool = True
    session_hours: int = Field(default=24, ge=1, le=168)
    invitation_hours: int = Field(default=72, ge=1, le=720)
    reset_hours: int = Field(default=1, ge=1, le=24)
    onelap_gateway: str = ""
    onelap_keys: list[SecretStr] = Field(default_factory=list)
    onelap_validated: bool = False
    onelap_interval_seconds: int = Field(default=900, ge=60, le=86400)
    onelap_history_days: int = Field(default=90, ge=1, le=365)
    onelap_request_interval_seconds: float = Field(default=1, ge=0.25, le=30)

    @property
    def database_file(self) -> Path:
        return private_storage_path(self.database_path)

    @property
    def fit_directory(self) -> Path:
        return private_storage_path(self.fit_path)

    @property
    def cookie_name(self) -> str:
        return "__Host-ctm_session" if self.secure_cookies else "ctm_session"
