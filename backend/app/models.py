from time import time

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> int:
    return int(time())


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[int] = mapped_column(default=now)
    updated_at: Mapped[int] = mapped_column(default=now, onupdate=now)


class User(Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('athlete', 'coach')"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(7), index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(default=True)
    activated_at: Mapped[int | None]

    @property
    def password_pending(self) -> bool:
        return self.password_hash is None


class AuthSession(Timestamps, Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[int] = mapped_column(index=True)


class Invitation(Timestamps, Base):
    __tablename__ = "invitations"
    __table_args__ = (CheckConstraint("kind IN ('activation', 'reset')"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    issued_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(10))
    expires_at: Mapped[int]
    consumed_at: Mapped[int | None]
    revoked_at: Mapped[int | None]


class LoginLimit(Base):
    __tablename__ = "login_limits"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    attempts: Mapped[int]
    expires_at: Mapped[int] = mapped_column(index=True)


class AuditLog(Timestamps, Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    target_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
