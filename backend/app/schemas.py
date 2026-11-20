from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, StringConstraints
from pydantic import TypeAdapter

Role = Literal["athlete", "coach"]
Action = Literal["activation", "reset"]

email_adapter = TypeAdapter(EmailStr)


def normalize_email(email: str) -> str:
    return email_adapter.validate_python(email.strip()).lower()


class Login(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class AcceptAccess(BaseModel):
    token: SecretStr = Field(min_length=43, max_length=43)
    password: SecretStr = Field(min_length=12, max_length=128)


class CreateInvitation(BaseModel):
    email: EmailStr
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    role: Role = "athlete"


class Identity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    role: Role


class Account(Identity):
    active: bool
    activated_at: int | None
    password_pending: bool


class SessionInfo(BaseModel):
    user: Identity
    csrf_token: str


class AccessLink(BaseModel):
    id: int
    kind: Action
    token: str
    expires_at: int


class InvitationInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    issued_by: int
    kind: Action
    expires_at: int
    consumed_at: int | None
    revoked_at: int | None


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: int | None
    target_id: int | None
    action: str
    created_at: int
