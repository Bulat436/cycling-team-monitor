"""Create, verify, or restore an operational SQLite/FIT snapshot."""
import argparse
import json
import re
import sqlite3
import stat
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from .config import Settings, private_storage_path

DATABASE = "team.sqlite3"
METADATA = "backup.json"
SETTINGS_FIELDS = {
    "fit_max_bytes", "allowed_origins", "secure_cookies", "session_hours", "invitation_hours",
    "reset_hours", "onelap_interval_seconds", "onelap_history_days", "onelap_request_interval_seconds",
}
FIT_PATH = re.compile(r"fit/[1-9][0-9]*/[0-9a-f]{64}\.fit")
MAX_FIT = 64 * 1024 * 1024
MAX_DATABASE = 64 * 1024**3


def open_readonly(path: Path):
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def inventory(db: sqlite3.Connection) -> dict[str, int]:
    result = {}
    for athlete, digest, size in db.execute("SELECT athlete_id, sha256, byte_count FROM workout_files"):
        path = f"fit/{athlete}/{digest}.fit"
        if not FIT_PATH.fullmatch(path) or not isinstance(size, int) or not 0 < size <= MAX_FIT:
            raise ValueError("Некорректная запись об оригинале FIT")
        result[path] = size
    return result


def check_database(db: sqlite3.Connection) -> str:
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("Проверка целостности SQLite не пройдена")
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Нарушены связи записей в SQLite")
    (migration,), = db.execute("SELECT version_num FROM alembic_version")
    return migration


def require_team_database(db: sqlite3.Connection) -> None:
    has_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='garmin_personal_connection'"
    ).fetchone()
    if has_table and db.execute("SELECT 1 FROM garmin_personal_connection LIMIT 1").fetchone():
        raise ValueError("Персональная база Garmin не входит в командный архив")


def clear_live_access(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA secure_delete=ON")
    with db:
        db.execute("DELETE FROM auth_sessions")
        db.execute("UPDATE invitations SET revoked_at=? WHERE revoked_at IS NULL AND consumed_at IS NULL", (int(time()),))
        db.execute("DELETE FROM login_limits")
        db.execute("UPDATE sync_runs SET status='cancelled', ended_at=?, diagnostic='connection_changed' "
                   "WHERE status IN ('queued', 'running')", (time(),))
        db.execute("UPDATE onelap_connections SET generation=generation+1, status='not_connected', secret=NULL, "
                   "account_hash=NULL, watermark=NULL, connect_attempt_at=NULL, last_success_at=NULL, "
                   "next_sync_at=NULL, failures=0, diagnostic=NULL")
        db.execute("DELETE FROM onelap_workout_versions")
        db.execute("DELETE FROM onelap_request_gate")
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("VACUUM")


def copy_checked(source, destination, size: int, expected: str | None = None) -> str:
    digest = sha256()
    left = size
    while left:
        chunk = source.read(min(left, 1024 * 1024))
        if not chunk:
            raise ValueError("Размер файла не соответствует описи")
        left -= len(chunk)
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    if source.read(1) or (expected and digest.hexdigest() != expected):
        raise ValueError("Размер или контрольная сумма файла не соответствует описи")
    return digest.hexdigest()


def backup_settings(settings: Settings) -> dict:
    data = settings.model_dump(mode="json", include=SETTINGS_FIELDS)
    for origin in data["allowed_origins"]:
        parts = urlsplit(origin)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
                or parts.path or parts.query or parts.fragment or origin != f"{parts.scheme}://{parts.netloc}"):
            raise ValueError("Допустимые origin должны содержать только схему, хост и порт")
    return data


def create_backup(settings: Settings, target: Path) -> None:
    safe_settings = backup_settings(settings)
    target = target.absolute()
    private_storage_path(target)
    if target.exists() or target.is_symlink():
        raise FileExistsError("Архив с таким именем уже существует")
    target.parent.mkdir(parents=True, exist_ok=True)
    fit_root = settings.fit_directory
    with TemporaryDirectory(dir=target.parent) as work:
        working = Path(work)
        snapshot = working / DATABASE
        with closing(open_readonly(settings.database_file)) as original, closing(sqlite3.connect(snapshot)) as db:
            require_team_database(original)
            original.backup(db, pages=256)
            clear_live_access(db)
            migration = check_database(db)
            files = inventory(db)

        size = snapshot.stat().st_size
        if not 0 < size <= MAX_DATABASE:
            raise ValueError("Размер SQLite выходит за пределы формата архива")
        temporary_archive = working / "snapshot.zip"
        with ZipFile(temporary_archive, "w", compression=ZIP_DEFLATED, allowZip64=True) as archive:
            with snapshot.open("rb") as source, archive.open(DATABASE, "w", force_zip64=True) as output:
                database_hash = copy_checked(source, output, size)
            for name, size in files.items():
                path = fit_root.joinpath(*name.split("/")[1:])
                if path.resolve() != path:
                    raise ValueError("Путь оригинала FIT выходит за ожидаемое расположение")
                with path.open("rb") as source, archive.open(name, "w") as output:
                    copy_checked(source, output, size, path.stem)
            archive.writestr(METADATA, json.dumps({
                "format": 1, "migration": migration, "database_sha256": database_hash,
                "settings": safe_settings,
            }, ensure_ascii=False, indent=2))
        # A hard link publishes the completed archive without ever overwriting another backup.
        target.hardlink_to(temporary_archive)


def checked_members(archive: ZipFile):
    members = {}
    for info in archive.infolist():
        name = info.filename
        if (name in members or name != info.orig_filename or info.is_dir()
                or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1):
            raise ValueError("Небезопасная запись в архиве")
        max_size = {METADATA: 65536, DATABASE: MAX_DATABASE}.get(name, MAX_FIT)
        if (name not in (METADATA, DATABASE) and not FIT_PATH.fullmatch(name)) or not 0 < info.file_size <= max_size:
            raise ValueError("Недопустимый путь или размер файла в архиве")
        members[name] = info
    if DATABASE not in members or METADATA not in members:
        raise ValueError("Отсутствуют обязательные файлы архива")
    return members


def unpack_verified(archive_path: Path, working: Path, *, restore: bool) -> None:
    with ZipFile(archive_path) as archive:
        members = checked_members(archive)
        metadata = json.loads(archive.read(METADATA))
        if not isinstance(metadata, dict) or metadata.get("format") != 1:
            raise ValueError("Неподдерживаемый формат архива")
        digest = metadata.get("database_sha256")
        settings = metadata.get("settings")
        if (not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
                or not isinstance(settings, dict) or settings.keys() != SETTINGS_FIELDS):
            raise ValueError("Некорректная опись архива")
        settings = backup_settings(Settings(_env_file=None, **settings))
        db_path = working / DATABASE
        with archive.open(DATABASE) as source, db_path.open("xb") as destination:
            copy_checked(source, destination, members[DATABASE].file_size, digest)
        with closing(open_readonly(db_path)) as db:
            if check_database(db) != metadata.get("migration"):
                raise ValueError("Версия схемы SQLite не соответствует описи")
            require_team_database(db)
            if db.execute("SELECT 1 FROM auth_sessions LIMIT 1").fetchone() or db.execute(
                "SELECT 1 FROM onelap_connections WHERE secret IS NOT NULL LIMIT 1"
            ).fetchone():
                raise ValueError("В архиве обнаружены действующие секреты")
            required = inventory(db)
        if members.keys() - {DATABASE, METADATA} != required.keys():
            raise ValueError("Состав FIT-файлов не соответствует базе")
        for name, size in required.items():
            if members[name].file_size != size:
                raise ValueError("Размер FIT-файла не соответствует базе")
            path = working.joinpath(*name.split("/"))
            with archive.open(name) as source:
                if restore:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as destination:
                        copy_checked(source, destination, size, path.stem)
                else:
                    copy_checked(source, None, size, path.stem)
        if restore:
            (working / "fit").mkdir(exist_ok=True)
            (working / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_backup(archive: Path) -> None:
    with TemporaryDirectory() as temporary:
        unpack_verified(archive, Path(temporary), restore=False)


def restore_backup(archive: Path, target: Path) -> None:
    target = target.absolute()
    private_storage_path(target)
    if not target.parent.is_dir():
        raise ValueError("Создайте родительский каталог восстановления")
    if target.exists() or target.is_symlink():
        raise FileExistsError("Каталог восстановления уже существует")
    with TemporaryDirectory(dir=target.parent) as temporary:
        staged = Path(temporary)
        unpack_verified(archive, staged, restore=True)
        # Reserve the directory before publishing. Never replace an existing directory.
        target.mkdir()
        try:
            for entry in staged.iterdir():
                entry.rename(target / entry.name)
        except OSError:
            # Keep the reserved directory for inspection if publication fails.
            if not any(target.iterdir()):
                target.rmdir()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    for action in ("create", "verify", "restore"):
        subcommand = subcommands.add_parser(action)
        subcommand.add_argument("archive", type=Path)
        if action == "restore":
            subcommand.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "create":
            create_backup(Settings(), args.archive)
        elif args.action == "verify":
            verify_backup(args.archive)
        else:
            restore_backup(args.archive, args.directory)
    except (OSError, ValueError, TypeError, sqlite3.Error, BadZipFile, RuntimeError) as exc:
        parser.exit(1, f"Операция не завершена ({type(exc).__name__}). Проверьте путь, формат и целостность данных.\n")
    print("Операция завершена.")


if __name__ == "__main__":
    main()
