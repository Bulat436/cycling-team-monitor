from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .athlete_models import Recorded
from .models import Base


class AlertRule(Recorded, Base):
    __tablename__ = "alerts"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool]


class AppSetting(Recorded, Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
