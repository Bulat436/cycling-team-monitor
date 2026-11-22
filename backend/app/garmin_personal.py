"""Run the Garmin Personal API as an owner-only Windows command, outside FastAPI."""

import argparse
import logging
import math
import os
import sqlite3
import sys
import warnings
from contextlib import closing
from datetime import date
from getpass import GetPassWarning, getpass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import portalocker
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import ROOT, Settings
from .database import make_engine
from .garmin_models import GarminPersonalConnection
from .garmin_provider import GarminError, valid_api_key
from .garmin_secrets import WindowsKeyVault
from .garmin_sync import GarminPersonalStore

DATABASE = "personal.sqlite3"


def personal_directory() -> Path:
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        raise GarminError("windows_profile_required")
    profile = Path(os.environ["LOCALAPPDATA"])
    if not profile.is_absolute():
        raise GarminError("windows_profile_required")
    private = (profile / "CyclingTeamMonitor" / "GarminPersonal").resolve()
    if private.is_relative_to(ROOT) or str(private).startswith("\\\\") or not private.is_relative_to(profile.resolve()):
        raise GarminError("private_directory_required")
    return private


def personal_settings(directory: Path) -> Settings:
    path = directory / DATABASE
    if path.is_symlink() or path.resolve() != path:
        raise GarminError("private_directory_required")
    return Settings(_env_file=None, database_path=path)


def initialize(settings: Settings, timezone: str):
    try:
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError):
        raise GarminError("invalid_timezone") from None
    target = settings.database_file
    if target.exists():
        raise GarminError("personal_database_already_exists")
    with TemporaryDirectory(dir=target.parent) as temporary:
        staging = Path(temporary) / DATABASE
        fresh = settings.model_copy(update={"database_path": staging})
        config = Config(ROOT / "backend" / "alembic.ini")
        config.attributes["settings"] = fresh
        command.upgrade(config, "head")
        engine = make_engine(fresh)
        try:
            with Session(engine) as db, db.begin():
                db.add(GarminPersonalConnection(id=1, installation_id=uuid4().hex, timezone=timezone))
        finally:
            engine.dispose()
        # Publish only the complete database, without relying on a staged WAL sidecar.
        with closing(sqlite3.connect(staging)) as db:
            mode = db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if mode != "delete":
                raise GarminError("personal_storage_unavailable")
        try:
            target.hardlink_to(staging)
        except FileExistsError:
            raise GarminError("personal_database_already_exists") from None


def prompt_key() -> str:
    if not sys.stdin.isatty():
        raise GarminError("private_terminal_required")
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        try:
            return getpass("Garmin Personal API key: ").strip()
        except (GetPassWarning, EOFError):
            raise GarminError("private_terminal_required") from None


def run(args, directory: Path):
    settings = personal_settings(directory)
    if args.action == "init":
        initialize(settings, args.timezone)
        print("personal_database_initialized")
        return
    if not settings.database_file.is_file():
        raise GarminError("personal_database_required")

    engine = make_engine(settings)
    try:
        store = GarminPersonalStore(engine)
        connection, last = store.status()
        vault = WindowsKeyVault(directory / "key.dpapi", connection.installation_id)
        if args.action == "connect":
            key = prompt_key()
            if not valid_api_key(key):
                raise GarminError("invalid_key")
            store.configure(None, None)
            vault.save(key)
            store.configure(args.history_days, args.request_interval_seconds)
            print("personal_key_configured")
        elif args.action == "disconnect":
            store.configure(None, None)
            vault.remove()
            print("personal_key_removed")
        elif args.action == "status":
            configured = connection.history_days is not None and vault.path.is_file()
            status = "configured" if configured else "not_connected"
            print(f"connection={status}; last_run={last.status if last else '-'}; last_success_epoch={connection.last_success_at or '-'}")
        else:
            if connection.history_days is None:
                raise GarminError("not_connected")
            result = store.sync(args.start, args.end, vault.load())
            print(f"{result.status}; inserted={result.inserted}; unchanged={result.unchanged}; skipped={result.skipped}")
    finally:
        engine.dispose()


class PersonalArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "invalid_command (see --help)\n")


def main() -> int:
    parser = PersonalArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init")
    init.add_argument("--timezone", required=True)
    connect = actions.add_parser("connect")
    connect.add_argument("--confirm-personal-terms", action="store_true", required=True)
    connect.add_argument("--history-days", type=int, required=True)
    connect.add_argument("--request-interval-seconds", type=int, required=True)
    sync = actions.add_parser("sync")
    sync.add_argument("--start", type=date.fromisoformat, required=True)
    sync.add_argument("--end", type=date.fromisoformat, required=True)
    actions.add_parser("status")
    actions.add_parser("disconnect")
    args = parser.parse_args()
    if args.action == "connect" and not (1 <= args.history_days <= 3660 and 1 <= args.request_interval_seconds <= 3600):
        parser.exit(2, "invalid_sync_policy (history days 1–3660, request interval seconds 1–3600)\n")
    # Suppress HTTP wire traces in this standalone command, even with debug logging enabled.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        directory = personal_directory()
        if args.action == "init":
            directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir():
            raise GarminError("personal_database_required")
        with portalocker.Lock(directory / "personal.lock", mode="a", timeout=0, fail_when_locked=True):
            run(args, directory)
        return 0
    except portalocker.exceptions.LockException:
        print("personal_command_in_progress", file=sys.stderr)
    except GarminError as error:
        print(error.code, file=sys.stderr)
        if error.retry_at is not None and math.isfinite(error.retry_at):
            print(f"retry_after_epoch={math.ceil(error.retry_at)}", file=sys.stderr)
    except (OSError, sqlite3.Error, SQLAlchemyError, ValueError):
        print("personal_storage_unavailable", file=sys.stderr)
    except KeyboardInterrupt:
        print("personal_command_interrupted", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
