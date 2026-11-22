import base64
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.athlete_models import WellnessEntry
from app.backup import create_backup
from app.config import Settings
from app.database import make_engine
from app.garmin_models import GarminSleepObservation, GarminSyncRun
from app.garmin_personal import initialize, main, personal_settings
from app.garmin_provider import ORIGIN, SLEEP_PATH, TOKEN_PATH, GarminClient, GarminError, retry_deadline
from app.garmin_secrets import WindowsKeyVault
from app.garmin_sync import GarminPersonalStore
from app.models import User

FAKE_KEY = "synthetic-key-for-httpx-test-only"
DAY = date(2026, 11, 19)


def fake_jwt(expiry):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    return "fake_header." + payload + ".fake_signature"


def night(**updates):
    return {
        "userProfilePK": 17, "id": 684, "calendarDate": DAY.isoformat(),
        "sleepStartTimestampGMT": "2026-11-19T13:30:00Z", "sleepEndTimestampGMT": "2026-11-19T22:00:00Z",
        "sleepStartTimestampLocal": "2026-11-19T16:30:00+03:00", "sleepEndTimestampLocal": "2026-11-20T01:00:00+03:00",
        "sleepTimeSeconds": 28_800, "deepSleepSeconds": 5400, "lightSleepSeconds": 17_400,
        "remSleepSeconds": 6000, "awakeSleepSeconds": 1800, "sleepScores": {"overall": {"value": 83}},
        "avgHeartRate": 47.5, **updates,
    }


class Clock:
    def __init__(self):
        self.current = datetime(2026, 11, 22, 12, tzinfo=UTC).timestamp()

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def personal(tmp_path, clock):
    settings = personal_settings(tmp_path)
    initialize(settings, "Europe/Moscow")
    engine = make_engine(settings)
    try:
        store = GarminPersonalStore(engine, clock=clock.now, wait=clock.advance)
        store.configure(90, 2)
        yield store, settings
    finally:
        engine.dispose()


def client(handler, clock, *, block=lambda _: None):
    return closing(GarminClient(
        FAKE_KEY, reserve=lambda: None, block=block, transport=httpx.MockTransport(handler),
        clock=clock.now, wait=clock.advance,
    ))


def provider(clock, rows):
    def handle(request):
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, text=fake_jwt(clock.now() + 3600))
        assert request.url.path == SLEEP_PATH
        return httpx.Response(200, json=rows)
    return httpx.MockTransport(handle)


def test_official_token_exchange_reuse_and_expiry(clock, caplog):
    requests = []
    tokens = []

    def handler(request):
        requests.append((request.method, request.headers["Authorization"]))
        assert str(request.url).startswith(ORIGIN)
        assert FAKE_KEY not in str(request.url)
        if request.url.path == TOKEN_PATH:
            tokens.append(fake_jwt(clock.now() + 60))
            return httpx.Response(200, text=tokens[-1])
        return httpx.Response(200, json=[])

    with caplog.at_level("DEBUG"), client(handler, clock) as garmin:
        for _ in range(2):
            list(garmin.sleep_batches(DAY, DAY))
        clock.advance(61)
        list(garmin.sleep_batches(DAY, DAY))
    assert requests == [
        ("POST", "ApiKey " + FAKE_KEY), ("GET", "Bearer " + tokens[0]), ("GET", "Bearer " + tokens[0]),
        ("POST", "ApiKey " + FAKE_KEY), ("GET", "Bearer " + tokens[1]),
    ]
    assert FAKE_KEY not in caplog.text and all(value not in caplog.text for value in tokens)


def test_inclusive_ranges_are_split_into_at_most_31_days(clock):
    windows = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, text=fake_jwt(clock.now() + 3600))
        params = request.url.params
        windows.append((date.fromisoformat(params["startDate"]), date.fromisoformat(params["endDate"])))
        return httpx.Response(200, json=[])

    start = DAY - timedelta(days=62)
    with client(handler, clock) as garmin:
        assert len(list(garmin.sleep_batches(start, DAY))) == 3
    assert windows == [
        (start, start + timedelta(days=30)),
        (start + timedelta(days=31), start + timedelta(days=61)), (DAY, DAY),
    ]


def test_import_versions_source_and_duplicate_rows(personal, clock):
    store, settings = personal
    first = night()
    updated = night(sleepTimeSeconds=28_000)
    for rows, expected in (([first, first], (1, 1)), ([updated], (1, 0)), ([first, updated], (0, 2))):
        result = store.sync(DAY, DAY, FAKE_KEY, transport=provider(clock, rows))
        assert (result.inserted, result.unchanged) == expected
        assert result.status == "success"
    with Session(store.engine) as db:
        earlier, later = db.scalars(select(GarminSleepObservation).order_by(GarminSleepObservation.id)).all()
        assert earlier.source_key == later.source_key
        assert earlier.external_id == later.external_id == 684
        assert earlier.source == "garmin" and earlier.received_at < later.received_at
        assert earlier.garmin_date == DAY and earlier.sleep_date == DAY + timedelta(days=1)
        assert earlier.timezone == "Europe/Moscow" and earlier.start_offset_minutes == earlier.end_offset_minutes == 180
        assert earlier.sleep_seconds == 28_800 and later.sleep_seconds == 28_000
        assert (earlier.deep_seconds, earlier.light_seconds, earlier.rem_seconds, earlier.awake_seconds) == (5400, 17_400, 6000, 1800)
        assert earlier.sleep_score == 83 and earlier.average_heart_rate == 47.5
        assert earlier.start_ms == 1795095000000 and earlier.end_ms == 1795125600000
        assert db.scalars(select(WellnessEntry)).all() == []
    assert FAKE_KEY.encode() not in settings.database_file.read_bytes()
    assert fake_jwt(clock.now() + 3600).encode() not in settings.database_file.read_bytes()


def test_optional_fields_invalid_rows_and_id_fallback(personal, clock):
    store, _ = personal
    no_id = night(
        id=None, deepSleepSeconds=None, awakeSleepSeconds=0, lightSleepSeconds=True, remSleepSeconds=-1,
        avgHeartRate=None, sleepScores={"duration": {"value": 82}}, sleepEndTimestampLocal=None,
    )
    incomplete = [night(sleepTimeSeconds=None), night(userProfilePK=None), night(calendarDate="wrong"),
                  night(sleepWindowConfirmed=False), night(sleepEndTimestampGMT=1795125600), "bad row"]
    first = store.sync(DAY, DAY, FAKE_KEY, transport=provider(clock, [no_id, *incomplete]))
    assert (first.status, first.inserted, first.skipped) == ("partial", 1, len(incomplete))
    second = store.sync(DAY, DAY, FAKE_KEY, transport=provider(clock, [no_id]))
    assert (second.inserted, second.unchanged) == (0, 1)
    third = store.sync(DAY, DAY, FAKE_KEY, transport=provider(clock, [night(), night(id=685)]))
    assert third.inserted == 2
    with Session(store.engine) as db:
        initial, assigned, distinct = db.scalars(select(GarminSleepObservation).order_by(GarminSleepObservation.id)).all()
        assert initial.external_id is None and initial.sleep_score is None and initial.average_heart_rate is None
        assert initial.deep_seconds is None and initial.light_seconds is None and initial.rem_seconds is None
        assert initial.awake_seconds == 0 and initial.end_offset_minutes is None
        assert initial.source_key == assigned.source_key != distinct.source_key


@pytest.mark.parametrize("status,code", [(400, "access_denied"), (401, "authentication_failed"), (403, "access_denied")])
def test_revoked_or_denied_key_never_retries_exchange(clock, status, code):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(status, text=FAKE_KEY)

    with client(handler, clock) as garmin, pytest.raises(GarminError, match=f"^{code}$"):
        list(garmin.sleep_batches(DAY, DAY))
    assert calls == [TOKEN_PATH]


@pytest.mark.parametrize("recover", [True, False])
def test_rejected_bearer_allows_one_new_exchange(clock, recover):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(200, text=fake_jwt(clock.now() + 3600 + len(calls)))
        if recover and len(calls) == 4:
            return httpx.Response(200, json=[])
        return httpx.Response(401)

    with client(handler, clock) as garmin:
        if recover:
            assert list(garmin.sleep_batches(DAY, DAY))[0].observations == []
        else:
            with pytest.raises(GarminError, match="^authentication_failed$"):
                list(garmin.sleep_batches(DAY, DAY))
    assert calls == [TOKEN_PATH, SLEEP_PATH] * 2


def test_rate_limit_survives_restart_and_covers_token_exchange(personal, clock):
    store, settings = personal
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "125"})

    transport = httpx.MockTransport(handler)
    until = clock.now() + 125
    with pytest.raises(GarminError, match="^rate_limited$") as limited:
        store.sync(DAY, DAY, FAKE_KEY, transport=transport)
    assert limited.value.retry_at == until
    store.engine.dispose()
    resumed_engine = make_engine(settings)
    try:
        resumed = GarminPersonalStore(resumed_engine, clock=clock.now, wait=clock.advance)
        with pytest.raises(GarminError, match="^rate_limited$"):
            resumed.sync(DAY, DAY, FAKE_KEY, transport=transport)
        assert calls == [TOKEN_PATH]
        clock.advance(125)
        assert resumed.sync(DAY, DAY, FAKE_KEY, transport=provider(clock, [night()])).inserted == 1
    finally:
        resumed_engine.dispose()


def test_retry_after_http_date_and_default(clock):
    instant = datetime(2026, 11, 22, 12, 3, tzinfo=UTC).timestamp()
    assert retry_deadline("Sun, 22 Nov 2026 12:03:00 GMT", clock.now()) == instant
    assert retry_deadline(FAKE_KEY, clock.now()) == clock.now() + 60


def test_unavailability_has_bounded_retries_and_safe_diagnostics(personal, clock, caplog):
    store, settings = personal
    calls = []
    token = fake_jwt(clock.now() + 3600)

    def unavailable(request):
        calls.append((request.url.path, clock.now()))
        if request.method == "POST":
            return httpx.Response(200, text=token)
        if len(calls) % 2 == 0:
            raise httpx.ReadTimeout(FAKE_KEY + token, request=request)
        return httpx.Response(503, text=FAKE_KEY + token)

    with caplog.at_level("DEBUG"), pytest.raises(GarminError, match="^unavailable$"):
        store.sync(DAY, DAY, FAKE_KEY, transport=httpx.MockTransport(unavailable))
    assert [path for path, _ in calls] == [TOKEN_PATH, SLEEP_PATH, SLEEP_PATH, SLEEP_PATH]
    assert all(second - first >= 2 for (_, first), (_, second) in zip(calls, calls[1:]))
    assert store.status()[1].status == "unavailable"
    assert FAKE_KEY not in caplog.text and token not in caplog.text
    with closing(sqlite3.connect(settings.database_file)) as db:
        dump = "\n".join(db.iterdump())
    assert FAKE_KEY not in dump and token not in dump


@pytest.mark.parametrize("payload", ['{"unexpected":[]}', '{"bad json"', FAKE_KEY])
def test_rejects_invalid_payload_without_echo(clock, payload):
    def handler(request):
        response = fake_jwt(clock.now() + 3600) if request.method == "POST" else payload
        return httpx.Response(200, text=response)
    with client(handler, clock) as garmin, pytest.raises(GarminError, match="^invalid_response$"):
        list(garmin.sleep_batches(DAY, DAY))


def test_redirect_is_never_followed(clock):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://example.test/"})

    with client(handler, clock) as garmin, pytest.raises(GarminError, match="^invalid_response$"):
        list(garmin.sleep_batches(DAY, DAY))
    assert calls == ["personal.connectapi.garmin.com"]


def test_single_garmin_account_and_no_mixed_batch(personal, clock, tmp_path):
    store, settings = personal
    mixed = [night(), night(id=999, userProfilePK=18)]
    with pytest.raises(GarminError, match="^account_mismatch$"):
        store.sync(DAY, DAY, FAKE_KEY, transport=provider(clock, mixed))
    assert store.status()[0].account_pk is None
    store.sync(DAY, DAY, FAKE_KEY, transport=provider(clock, [night()]))
    store.configure(None, None)
    store.configure(90, 2)
    with pytest.raises(GarminError, match="^account_mismatch$"):
        store.sync(DAY, DAY, FAKE_KEY, transport=provider(clock, mixed[1:]))
    assert store.status()[0].account_pk == 17
    with Session(store.engine) as db:
        assert [row.external_id for row in db.scalars(select(GarminSleepObservation))] == [684]
    with pytest.raises(ValueError, match="Персональная база Garmin"):
        create_backup(settings, tmp_path / "team_backup.zip")


def test_permitted_history_is_enforced_before_any_request(personal):
    store, _ = personal
    store.configure(2, 1)
    with pytest.raises(GarminError, match="^invalid_range$"):
        store.sync(DAY, DAY, FAKE_KEY, transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected request")))
    assert store.status()[1] is None


def test_cli_arguments_do_not_echo_a_misplaced_secret(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["garmin_personal", "status", "--key", FAKE_KEY])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 2
    assert FAKE_KEY not in capsys.readouterr().err


def test_empty_sync_and_disconnect_leave_the_imported_night_intact(personal, clock):
    store, _ = personal
    store.sync(DAY, DAY, FAKE_KEY, transport=provider(clock, [night()]))
    assert store.sync(DAY, DAY, FAKE_KEY, transport=provider(clock, [])).status == "success"
    store.configure(None, None)
    with pytest.raises(GarminError, match="^not_connected$"):
        store.sync(DAY, DAY, FAKE_KEY, transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected request")))
    with Session(store.engine) as db:
        assert db.scalars(select(GarminSleepObservation.external_id)).all() == [684]


def test_migration_and_rollback_preserve_historical_diary_and_reject_team_import(migration, clock):
    path, config = migration
    command.upgrade(config, "0007_head_coach")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO users (id, email, name, role, created_at, updated_at, active) VALUES (1, 'g6@example.test', 'Test', 'athlete', 1, 1, 1)")
        db.execute("INSERT INTO athlete_profiles (athlete_id, timezone, created_at, updated_at) VALUES (1, 'Europe/Moscow', 1, 1)")
        db.executemany(
            "INSERT INTO wellness_entries (athlete_id, entry_date, timezone, sleep_quality, general_fatigue, leg_fatigue, "
            "motivation, mood, muscle_soreness, illness_symptoms, sleep_minutes, recovery, training_day_status, created_at, updated_at) "
            "VALUES (1, ?, 'Europe/Moscow', 3, 2, 4, 4, 3, 2, 0, ?, 3, 'rest', 1, 2)",
            [("2026-11-18", 450), ("2026-11-19", None), ("2026-11-20", 0)],
        )
        before = db.execute("SELECT * FROM wellness_entries ORDER BY entry_date").fetchall()
    command.upgrade(config, "head")
    engine = make_engine(Settings(_env_file=None, database_path=path))
    try:
        store = GarminPersonalStore(engine, clock=clock.now, wait=clock.advance)
        with pytest.raises(GarminError, match="^personal_database_required$"):
            store.sync(DAY, DAY, FAKE_KEY, transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected request")))
        with Session(engine) as db:
            assert db.scalars(select(GarminSleepObservation)).all() == []
            assert db.scalars(select(GarminSyncRun)).all() == []
    finally:
        engine.dispose()
    for migrate, target in ((command.downgrade, "0007_head_coach"), (command.upgrade, "head")):
        migrate(config, target)
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT * FROM wellness_entries ORDER BY entry_date").fetchall() == before
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='garmin_sleep_observations'").fetchone()
            assert bool(exists) == (target == "head")


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_dpapi_vault_encrypts_binds_and_removes_fake_key(tmp_path):
    path = tmp_path / "key.dpapi"
    vault = WindowsKeyVault(path, "ab" * 16)
    vault.save(FAKE_KEY)
    assert FAKE_KEY.encode() not in path.read_bytes()
    assert vault.load() == FAKE_KEY
    with pytest.raises(GarminError, match="^credential_unavailable$"):
        WindowsKeyVault(path, "cd" * 16).load()
    vault.remove()
    assert not path.exists()
