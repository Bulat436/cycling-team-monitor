import argparse
import sys
from getpass import getpass

from pydantic import ValidationError
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .config import Settings
from .database import make_engine
from .models import AuthSession, User, now
from .onelap_models import OnelapConnection
from .onelap_provider import ProviderError, gateway_origin
from .onelap_secrets import SessionVault
from .schemas import normalize_email
from .security import audit, passwords, revoke_access_links


def validate_credentials(email: str, password: str) -> str:
    try:
        email = normalize_email(email)
    except ValidationError:
        raise ValueError("Укажите действительный email.") from None
    if not 12 <= len(password) <= 128:
        raise ValueError("Пароль должен содержать 12–128 символов.")
    return email


def bootstrap_coach(settings: Settings, email: str, name: str, password: str) -> None:
    email = validate_credentials(email, password)
    name = name.strip()
    if not 1 <= len(name) <= 120:
        raise ValueError("Имя должно содержать 1–120 символов.")
    engine = make_engine(settings)
    try:
        with Session(engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            if db.scalar(select(User.id).where(User.role == "coach").limit(1)):
                raise ValueError("Первый тренер уже создан.")
            user = User(
                email=email, name=name, role="coach",
                password_hash=passwords.hash(password), activated_at=now(),
            )
            db.add(user)
            db.flush()
            audit(db, "coach_bootstrapped", None, user.id)
            db.commit()
    finally:
        engine.dispose()


def reset_coach_password(settings: Settings, email: str, password: str) -> None:
    email = validate_credentials(email, password)
    engine = make_engine(settings)
    try:
        with Session(engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            coach = db.scalar(select(User).where(
                User.email == email, User.role == "coach",
                User.active.is_(True), User.activated_at.is_not(None),
            ))
            if not coach:
                raise ValueError("Активная учётная запись тренера не найдена.")
            coach.password_hash = passwords.hash(password)
            revoke_access_links(db, coach.id)
            db.execute(delete(AuthSession).where(AuthSession.user_id == coach.id))
            audit(db, "coach_password_reset_by_operator", None, coach.id)
            db.commit()
    finally:
        engine.dispose()


def rotate_onelap_keys(settings: Settings) -> int:
    if len(settings.onelap_keys) < 2 or not settings.onelap_gateway:
        raise ValueError("Укажите HTTPS-адрес шлюза и ключи в порядке от нового к старому.")
    vault = SessionVault(settings.onelap_keys, gateway_origin(settings.onelap_gateway))
    if vault.cipher is None:
        raise ValueError("Не удалось прочитать ключи Fernet.")
    engine = make_engine(settings)
    try:
        with Session(engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            rows = db.scalars(select(OnelapConnection).where(OnelapConnection.secret.is_not(None))).all()
            for row in rows:
                session = vault.open(row.athlete_id, row.generation, row.secret)
                row.secret = vault.seal(row.athlete_id, row.generation, session)
            db.commit()
            return len(rows)
    except ProviderError:
        raise ValueError("Ротация отменена. Проверьте прежние ключи и адрес шлюза.") from None
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальное администрирование Cycling Team Monitor")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-coach", help="Создать первого тренера")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--name", required=True)
    reset = commands.add_parser("reset-coach-password", help="Восстановить доступ активного тренера")
    reset.add_argument("--email", required=True)
    commands.add_parser("rotate-onelap-keys", help="Перешифровать сессии OnelapFit новым ключом")
    args = parser.parse_args()
    if args.command == "rotate-onelap-keys":
        try:
            count = rotate_onelap_keys(Settings())
        except ValueError as exc:
            parser.exit(1, f"{exc}\n")
        print(f"Перешифровано сессий: {count}")
        return
    if not sys.stdin.isatty():
        parser.exit(1, "Запустите команду в интерактивном терминале для скрытого ввода пароля.\n")
    password = getpass("Пароль (12–128 символов): ")
    if getpass("Повторите пароль: ") != password:
        parser.exit(1, "Пароли не совпадают.\n")
    try:
        if args.command == "bootstrap-coach":
            bootstrap_coach(Settings(), args.email, args.name, password)
            print("Первый тренер создан.")
        else:
            reset_coach_password(Settings(), args.email, password)
            print("Пароль тренера изменён, предыдущие сессии завершены.")
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
