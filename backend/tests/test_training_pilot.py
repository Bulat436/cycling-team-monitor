from collections import Counter
from contextlib import closing
from contextlib import contextmanager
from datetime import timedelta
from math import ceil, exp, fsum
from pathlib import Path
from secrets import token_urlsafe
import socket
import sqlite3
from statistics import correlation
import subprocess
import sys
from time import perf_counter, sleep
from uuid import uuid4

import httpx
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.backup import create_backup, restore_backup, verify_backup
from app.config import Settings
from app.database import make_engine
from app.main import create_app
from app.training_intelligence import routes
from app.training_intelligence.load import daily_load
from app.training_intelligence.recovery import recovery_days
from app.training_intelligence.relationships import episodes, lag_correlations
from app.training_intelligence.schemas import Reaction, Rules, WorkoutLoad
from app.workout_import import WorkoutImportService
from conftest import ORIGIN
from fit_fixtures import cycling_fit
from seed_training_pilot import (
    AS_OF, CASES, CONTROL_DAYS, athlete_email, fixture_sleep, pilot_day, seed_training_pilot, workout_time,
)
from test_athletes import API, sign_in, write
from test_workout_import import update_ftp, upload

MODULE = API + "/training-intelligence"
DAY = timedelta(days=1)
ALERT_CODES = {
    "possible_accumulated_fatigue": "A",
    "load_growth_with_worsening_recovery": "B",
    "possible_performance_decline": "C",
    "positive_adaptation_signs": "D",
    "insufficient_data": "E",
    "recovery_after_load_block": "F",
}


@pytest.fixture(scope="module")
def pilot(tmp_path_factory):
    password = token_urlsafe(24)
    directory = tmp_path_factory.mktemp("g5") / "pilot"
    return seed_training_pilot(directory, password), password


@pytest.fixture
def coach(pilot, coach_cookies, monkeypatch):
    settings, _ = pilot
    monkeypatch.setattr(routes, "time", lambda: AS_OF.timestamp())
    with TestClient(create_app(settings, start_onelap_worker=False), base_url=ORIGIN, cookies=coach_cookies) as client:
        yield client


@pytest.fixture(scope="module")
def coach_cookies(pilot):
    settings, password = pilot
    with TestClient(create_app(settings, start_onelap_worker=False), base_url=ORIGIN) as client:
        sign_in(client, "pilot-coach1@example.com", password)
        return dict(client.cookies)


def get_json(client, path, **kwargs):
    response = client.get(path, **kwargs)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def team_by_case(client):
    return {row["name"].split(" · ")[1]: row for row in get_json(client, MODULE + "/team")["athletes"]}


def athlete_path(row):
    return f"{MODULE}/athletes/{row['athlete_id']}"


def measured(client, row):
    return get_json(client, athlete_path(row) + "/measurements?period=90")


def exact_signals(row):
    return tuple(sorted(ALERT_CODES[warning["type"]] + ":" + warning["attention"][0] for warning in row["warnings"]))


def test_g5_twenty_expected_warning_sets_and_three_coaches(coach, pilot):
    _, password = pilot
    result = team_by_case(coach)
    assert len(result) == 20
    for case in CASES:
        row = result[case.key]
        assert exact_signals(row) == case.expected, case.key
        assert row["completed_through"] == pilot_day(48).isoformat()
        assert get_json(coach, athlete_path(row) + "/warnings")["warnings"] == row["warnings"]
        warnings = {warning["id"]: warning for warning in row["warnings"]}
        assert len(warnings) == len(row["warnings"])
        for warning in warnings.values():
            assert warning["period"]["start"] <= warning["period"]["end"]
            assert warning["facts"] and warning["limitations"]
            assert "G5_PRIVATE_SYNTHETIC_DIARY" not in str(warning)
            if warning["type"] != "insufficient_data":
                # Positive evidence should keep the interpretation limited to an observation.
                assert any("не устанавливает причинную связь" in text for text in warning["limitations"])
                assert not any(phrase in warning["explanation"].casefold() for phrase in (
                    "нагрузка вызвала", "доказывает", "стало причиной", "диагноз",
                ))
            for related_id in warning["related_warning_ids"]:
                assert warning["id"] in warnings[related_id]["related_warning_ids"]
                assert warning["episode_id"] == warnings[related_id]["episode_id"]
            for fact in warning["facts"]:
                if fact["delta"] is None:
                    continue
                assert fact["delta"] == pytest.approx(fact["value"] - fact["baseline"])
                if fact["delta_pct"] is not None:
                    assert fact["delta_pct"] == pytest.approx(100 * (fact["value"] / fact["baseline"] - 1))
    for number in (2, 3):
        with TestClient(coach.app, base_url=ORIGIN) as colleague:
            sign_in(colleague, f"pilot-coach{number}@example.com", password)
            assert team_by_case(colleague) == result


def test_g5_api_observations_have_the_expected_numeric_meaning(coach):
    rows = team_by_case(coach)
    high = rows["same-dose-high"]
    normal = rows["same-dose-normal"]
    assert high["current_week"]["recorded_tss"] == normal["current_week"]["recorded_tss"] == 700
    assert high["current_week"]["baseline_tss"] == 525
    assert normal["current_week"]["baseline_tss"] == 784
    assert high["current_week"]["delta_pct"] == pytest.approx(100 / 3)
    assert normal["current_week"]["delta_pct"] == pytest.approx(-75 / 7)
    for key, expected in (("heavy-streak", 50), ("growth-at-threshold", 15), ("fatigue-at-threshold", 20)):
        warning = rows[key]["warnings"][0]
        load = next(fact for fact in warning["facts"] if fact["metric"] == "weekly_tss")
        assert load["delta_pct"] == pytest.approx(expected)
    assert rows["heavy-streak"]["warnings"][0]["attention"] == "high"
    assert len(rows["heavy-streak"]["warnings"][0]["coverage"]["worsened_recovery_dates"]) == 3

    sleep = measured(coach, rows["normal-bad-sleep"])
    point = next(day for day in sleep["recovery"] if day["date"] == pilot_day(45).isoformat())
    assert point["metrics"]["sleep_minutes"]["baseline"] == fixture_sleep(3)
    assert point["metrics"]["sleep_minutes"]["delta"] == -60
    assert point["metrics"]["sleep_minutes"]["observations"] == 28
    assert rows["normal-bad-sleep"]["recovery"]["sustained"] is False

    for key, deltas in (("growth-improved", [10, 12]), ("growth-declined", [-10, -8]), ("decline-at-threshold", [-5, -5])):
        warning = rows[key]["warnings"][0]
        facts = [fact for fact in warning["facts"] if fact["metric"] == "control_mean_power"]
        assert [fact["delta_pct"] for fact in facts] == deltas
        assert [fact["baseline"] for fact in facts] == [200, 200]
        assert [fact["date"] for fact in facts] == [pilot_day(index).isoformat() for index in CONTROL_DAYS[-2:]]
        assert warning["coverage"]["performance_baseline_attempts"] == 3
        assert warning["coverage"]["performance_recent_attempts"] == 2
    incomparable = measured(coach, rows["incomparable-protocol"])
    assert {point["protocol"] for point in incomparable["performance"]["efforts"]} == {"pilot-60s", "other-60s"}
    assert not incomparable["performance"]["repeated_changes"]
    restored = rows["recovery-after-drop"]["warnings"][0]
    assert restored["coverage"]["evaluated_recovery_dates"] == [pilot_day(index).isoformat() for index in (44, 45)]

    missing = rows["missing-diary"]
    assert missing["recovery"]["sustained"] is None and not missing["recovery"]["evaluated_dates"]
    assert missing["warnings"][0]["coverage"]["diary_filled_days"] == 4
    for case, status, amount, known in (
        ("confirmed-rest", "all_workouts_recorded", 100, 7),
        ("missing-fit", "unknown", None, 4),
        ("rest-conflicts-with-fit", "conflict", None, 6),
    ):
        details = measured(coach, rows[case])
        last = details["load"][-2]
        assert (last["day_status"], last["tss"]) == (status, amount)
        assert details["current_week"]["coverage"]["known_days"] == known
        if case == "confirmed-rest":
            assert details["current_week"]["rest_days"] == 2
            assert details["current_week"]["recorded_tss"] == 500
            assert [(item["tss"], item["day_status"]) for item in details["load"][-8:-1] if item["tss"] == 0] == [
                (0, "rest"), (0, "rest"),
            ]
    short = measured(coach, rows["short-history"])
    assert short["current_week"]["baseline_tss"] is None
    assert short["load"][-2]["baseline"] is None
    assert short["recovery"][-2]["metrics"]["sleep_minutes"]["baseline"] is None


def expected_ewma(series, day, period):
    """Evaluate the finite weighted sum directly, without retaining recurrence state."""
    source = [(index, value) for index, value in enumerate(series[:day + 1]) if value is not None]
    if series[day] is None:
        return None
    first, *following = source
    seed_w = exp((first[0] - day) / period)
    weights = [(1 - exp(-1 / period)) * exp((index - day) / period) for index, _ in following]
    return fsum([seed_w * first[1], *(w * sample[1] for w, sample in zip(weights, following, strict=True))]) / (
        seed_w + fsum(weights)
    )


def source_load(day, amount, workout_id):
    return WorkoutLoad(workout_id=workout_id, date=day, started_at=0, timer_seconds=None, np=None, if_value=None,
                       tss=amount, rpe=None, session_rpe=None, intense=False)


def test_g5_ewma_closed_form_seed_gap_zero_and_coverage(coach):
    today = pilot_day(49)
    start = today - 60 * DAY
    source = [None, 80, None, 0, 150] + [100 + index % 3 for index in range(54)] + [None, 70]
    rides = [source_load(start + index * DAY, value, index + 1) for index, value in enumerate(source) if value]
    marks = {start + index * DAY: "all_workouts_recorded" if value else "rest"
             for index, value in enumerate(source[:-1]) if value is not None}
    for index, point in enumerate(daily_load(start, today, rides, marks, Rules())):
        atl, ctl = (expected_ewma(source, index, period) for period in (7, 42))
        assert point.atl == pytest.approx(atl) if atl is not None else point.atl is None
        assert point.ctl == pytest.approx(ctl) if ctl is not None else point.ctl is None
        assert point.tsb == pytest.approx(ctl - atl) if atl is not None else point.tsb is None
        if index == 1:
            assert point.atl == point.ctl == 80 and point.tsb == 0 and point.ctl_status == "insufficient"
        if index == 59:
            assert point.atl_status == "missing"
        if index == 60:
            assert point.preliminary and point.atl_status == point.ctl_status == "partial"
    rest = team_by_case(coach)["confirmed-rest"]
    final = measured(coach, rest)["load"][-2]
    planned = [0 if index % 7 in {1, 4} else 100 for index in range(49)]
    assert final["atl"] == pytest.approx(expected_ewma(planned, 48, 7))
    assert final["ctl"] == pytest.approx(expected_ewma(planned, 48, 42))
    assert final["tsb"] == pytest.approx(final["ctl"] - final["atl"])


def test_g5_28_day_median_excludes_evaluated_day_and_rejects_six_samples():
    target = pilot_day(48)
    entries = {
        target - lag * DAY: {"sleep_minutes": minutes}
        for lag, minutes in ((29, 250), (28, 320), (22, 350), (18, 400), (12, 440), (9, 480), (4, 500), (1, 540), (0, 20))
    }
    observed = recovery_days(target, target, entries, Rules())[0].metrics["sleep_minutes"]
    assert (observed.value, observed.baseline, observed.delta, observed.observations) == (20, 440, -420, 7)
    del entries[target - 28 * DAY]
    short = recovery_days(target, target, entries, Rules())[0].metrics["sleep_minutes"]
    assert (short.baseline, short.delta, short.observations, short.deteriorated) == (None, None, 6, None)


def test_g5_spearman_ties_stability_and_seven_day_alignment_against_reference():
    first = pilot_day(0)
    load = {first + index * DAY: index // 3 for index in range(40)}
    ties = [index // 4 + index % 3 for index in range(40)]
    outcome = {first + (index + 7) * DAY: value for index, value in enumerate(ties)}
    changed = {day: index if index < 20 else 59 - index for index, day in enumerate(load)}
    observations = lag_correlations(load, set(load), {
        "ties": outcome, "contradictory_halves": changed, "short": dict(list(outcome.items())[:29]),
    }, first + 46 * DAY, Rules())
    by_lag = {(row.metric, row.lag_days): row for row in observations}

    def reference_ranks(sequence):
        return [sum(1.0 if other < value else 0.5 if other == value else 0.0 for other in sequence)
                + 0.5 for value in sequence]

    expected = correlation(reference_ranks(list(load.values())), reference_ranks(ties))
    at_seven = by_lag["ties", 7]
    assert at_seven.coefficient == pytest.approx(expected)
    assert at_seven.pairs == at_seven.confirmed_load_pairs == 40 and at_seven.stable
    assert by_lag["ties", 0].pairs == 33
    assert by_lag["short", 7].coefficient is None
    contradiction = by_lag["contradictory_halves", 0]
    assert contradiction.halves[0] > 0 and contradiction.halves[1] < 0
    assert not contradiction.stable and contradiction.status == "partial"


def test_g5_episode_partition_has_unique_reactions_and_explicit_nearest_dates():
    first = pilot_day(0)
    offsets = (0, 3, 11)
    rides = [source_load(first + offset * DAY, 180, offset + 1) for offset in offsets]
    daily = daily_load(first, first + 20 * DAY, rides, {}, Rules())
    reactions = [Reaction(date=first + offset * DAY, metric="sleep_quality", value=2, baseline=4, delta=-2)
                 for offset in (-1, 0, 1, 2, 3, 6, 10, 11, 18, 19)]
    one, two = episodes([daily[offset] for offset in offsets], reactions + reactions, {}, first + 19 * DAY, 2)
    assert one.workout_ids == [1, 4] and two.workout_ids == [12]
    assert one.reaction_window_end == first + 10 * DAY and two.reaction_window_end == first + 18 * DAY
    assert [item.lag_days for item in one.reactions] == [0, 1, 2, 0, 3, 7]
    assert [item.lag_days for item in two.reactions] == [0, 7]
    assert [item.load_date for item in one.reactions] == [first] * 3 + [first + 3 * DAY] * 3
    assert all(item.same_date_only == (item.lag_days == 0) for item in one.reactions + two.reactions)


def test_g5_scope_validation_duplicate_fit_and_historical_ftp(coach, pilot):
    settings, password = pilot
    rows = team_by_case(coach)
    member = rows["growth-declined"]
    other = rows["high-worsening"]
    valid = measured(coach, member)
    original = next(point for point in valid["performance"]["efforts"] if point["date"] == pilot_day(32).isoformat())
    workout_id = original["workout_id"]
    ride = f"/workouts/{workout_id}"
    effort = f"/training-intelligence/workouts/{workout_id}/effort"
    old_projection = get_json(coach, API + ride + "/metrics")
    assert old_projection["snapshot"]["ftp_watts"] == 250
    assert old_projection["result"]["tss"] == pytest.approx((210 / 250) ** 2 * 100 / 30)
    latest = valid["performance"]["efforts"][-1]["workout_id"]
    assert get_json(coach, API + f"/workouts/{latest}/metrics")["snapshot"]["ftp_watts"] == 300
    assert coach.get(MODULE + "/athletes/999999/warnings").json() == {"detail": "athlete_missing"}
    csrf = get_json(coach, API + "/auth/me")["csrf_token"]
    rejected = write(coach, "PUT", effort, {"protocol": "G5_PRIVATE_INPUT", "start_seconds": -1, "duration_seconds": 60}, csrf)
    assert rejected.status_code == 422 and rejected.json() == {"detail": "validation_error"}
    assert not rejected.text.count("G5_PRIVATE_INPUT")
    assert coach.get(athlete_path(member) + "/measurements?period=G5_PRIVATE_INPUT").json() == {"detail": "validation_error"}
    assert write(coach, "PUT", effort, {"protocol": "pilot-60s", "start_seconds": 100, "duration_seconds": 60}, csrf).json() == {
        "detail": "incomplete_effort_power",
    }
    assert update_ftp(coach, csrf, member["athlete_id"], pilot_day(32).isoformat(), 280).status_code == 200
    assert get_json(coach, API + ride + "/metrics") == old_projection

    with TestClient(create_app(settings, start_onelap_worker=False), base_url=ORIGIN) as client:
        for path in (MODULE + "/team", athlete_path(member) + "/warnings", API + effort, MODULE + "/me"):
            assert client.get(path).status_code == 401
        _, athlete_csrf = sign_in(client, athlete_email(CASES[6]), password)
        assert client.get(MODULE + "/team").status_code == 403
        assert client.get(athlete_path(member) + "/warnings").status_code == 403
        assert client.get(athlete_path(other) + "/measurements").json() == {"detail": "athlete_missing"}
        assert client.get(API + effort).status_code == 403
        assert get_json(client, MODULE + "/me?athlete_id=" + str(other["athlete_id"]))["athlete_id"] == member["athlete_id"]
        fit = client.get(API + ride + "/fit")
        assert fit.status_code == 200 and fit.headers["cache-control"] == "no-store"
        repeated = upload(client, fit.content, athlete_csrf, filename="different-name.fit")
        assert repeated.status_code == 200 and repeated.json()["status"] == "duplicate"
        assert repeated.json()["workout_id"] == workout_id
        damaged = upload(client, fit.content[:-1] + bytes([fit.content[-1] ^ 1]), athlete_csrf, filename="G5_PRIVATE_INPUT.fit")
        assert damaged.status_code == 422 and damaged.json() == {"detail": "fit_bad_crc"}
        assert "G5_PRIVATE_INPUT" not in damaged.text
    rechecked = measured(coach, member)["performance"]
    assert rechecked["efforts"] == valid["performance"]["efforts"]
    assert rechecked["repeated_changes"] == valid["performance"]["repeated_changes"]
    assert rechecked["ftp"] != valid["performance"]["ftp"]


def test_g5_upgrade_from_0005_preserves_actual_fit_and_old_metrics(migration):
    path, config = migration
    settings = Settings(_env_file=None, database_path=path, fit_path=path.parent / "fit")
    config.attributes["settings"] = settings
    command.upgrade(config, "head")
    day = pilot_day(0)
    activity = cycling_fit(start=workout_time(day), power=200)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO users (id,email,name,role,active,created_at,updated_at) "
                   "VALUES (1,'legacy-fit@example.com','Synthetic','athlete',1,1,1)")
        db.execute("INSERT INTO athlete_profiles (athlete_id,timezone,created_at,updated_at) VALUES (1,'UTC',1,1)")
        db.execute("INSERT INTO ftp_history (athlete_id,effective_from,watts,created_at,updated_at) "
                   "VALUES (1,?,250,1,1)", (day.isoformat(),))
    engine = make_engine(settings)
    try:
        result = WorkoutImportService(engine, settings).import_fit(1, activity, filename="legacy-synthetic.fit")
    finally:
        engine.dispose()
    # Prepare the legacy database without running current ORM models against an old schema.
    command.downgrade(config, "0005_coach")
    queries = (
        "SELECT id,started_at,timer_seconds,file_sha256,summary FROM workouts",
        "SELECT workout_id,snapshot,result FROM workout_metrics",
        "SELECT workout_id,block_index,payload FROM workout_samples",
        "SELECT athlete_id,workout_id,sha256,fit_sha256 FROM workout_files",
    )
    with closing(sqlite3.connect(path)) as db:
        before = [db.execute(query).fetchall() for query in queries]
    assert all(before) and before[0][0][0] == result.workout_id
    original = settings.fit_directory / "1" / (before[0][0][3] + ".fit")
    command.upgrade(config, "head")
    command.check(config)
    with closing(sqlite3.connect(path)) as db:
        assert [db.execute(query).fetchall() for query in queries] == before
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert original.read_bytes() == activity


def test_g5_backup_restore_and_restart_preserve_pilot_analytics(coach, pilot, tmp_path):
    settings, password = pilot
    snapshot = team_by_case(coach)
    target = snapshot["growth-improved"]
    reference = measured(coach, target)
    fit_id = reference["performance"]["efforts"][0]["workout_id"]
    fit_route = f"{API}/workouts/{fit_id}/fit"
    original = coach.get(fit_route).content
    archive = tmp_path / "g5-backup.zip"
    create_backup(settings, archive)
    verify_backup(archive)
    recovered = tmp_path / "recovered"
    restore_backup(archive, recovered)
    restored = settings.model_copy(update={"database_path": recovered / "team.sqlite3", "fit_path": recovered / "fit"})
    with TestClient(create_app(restored, start_onelap_worker=False), base_url=ORIGIN, cookies=coach.cookies) as client:
        assert client.get(MODULE + "/team").status_code == 401
        sign_in(client, "pilot-coach1@example.com", password)
        assert team_by_case(client) == snapshot
        assert measured(client, target) == reference
        response = client.get(fit_route)
        assert response.status_code == 200 and response.content == original
        cookies = client.cookies
    with TestClient(create_app(restored, start_onelap_worker=False), base_url=ORIGIN, cookies=cookies) as restarted:
        assert team_by_case(restarted) == snapshot


WINDOWS_PILOT_PROCESS = """
import asyncio
import sys
from pathlib import Path
from app import main, windows_server
from app.config import Settings
from app.training_intelligence import routes

runtime, run_id, port, database, fit, static_root, stamp = sys.argv[1:]
origin = f"http://127.0.0.1:{port}"
settings = Settings(_env_file=None, database_path=Path(database), fit_path=Path(fit),
                    allowed_origins=[origin], secure_cookies=False, onelap_gateway="", onelap_keys=[])
windows_server.Settings = lambda **overrides: settings.model_copy(update=overrides)
main.ROOT = Path(static_root)
routes.time = lambda: float(stamp)
asyncio.run(windows_server.serve(Path(runtime), run_id, int(port)))
"""


@contextmanager
def pilot_windows_process(settings, tmp_path, port):
    runtime = tmp_path / "runtime"
    log = tmp_path / "server.log"
    run_id = uuid4().hex
    args = [
        sys.executable, "-c", WINDOWS_PILOT_PROCESS, str(runtime), run_id, str(port),
        str(settings.database_file), str(settings.fit_directory), str(tmp_path), str(AS_OF.timestamp()),
    ]
    with log.open("wb") as output:
        process = subprocess.Popen(
            args, cwd=Path(__file__).resolve().parents[1], stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            deadline = perf_counter() + 15
            marker = runtime / "running.json"
            while not marker.is_file() and process.poll() is None and perf_counter() < deadline:
                sleep(.1)
            assert marker.is_file() and process.poll() is None, log.read_text(encoding="utf-8", errors="replace")
            yield process.pid
            (runtime / f"{run_id}.stop").touch()
            assert process.wait(timeout=15) == 0, log.read_text(encoding="utf-8", errors="replace")
            assert not marker.exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runtime check")
def test_g5_windows_process_stops_and_restarts_on_isolated_settings(pilot, tmp_path):
    settings, password = pilot
    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "icons").mkdir()
    for name in ("index.html", "manifest.webmanifest", "sw.js"):
        (dist / name).touch()
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=origin, trust_env=False) as client:
        with pilot_windows_process(settings, tmp_path, port) as first_pid:
            assert get_json(client, API + "/health") == {"status": "ok"}
            login = client.post(API + "/auth/login", headers={"Origin": origin}, json={
                "email": "pilot-coach1@example.com", "password": password,
            })
            assert login.status_code == 200
            before = team_by_case(client)
            assert len(before) == 20
        with pilot_windows_process(settings, tmp_path, port) as second_pid:
            assert second_pid != first_pid
            assert get_json(client, API + "/health") == {"status": "ok"}
            assert team_by_case(client) == before


def test_g5_benchmark_twenty_requests_and_batching(coach):
    queries = Counter()

    def count_sql(_, __, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            queries["selects"] += 1
            for table in ("workouts", "wellness_entries", "workout_samples"):
                if f"from {table} " in " ".join(statement.casefold().split()) + " ":
                    queries[table] += 1

    def run(path, n=20):
        timings = []
        counts = []
        for _ in range(n):
            queries.clear()
            start = perf_counter()
            payload = get_json(coach, path)
            timings.append(1000 * (perf_counter() - start))
            counts.append(queries.copy())
        return timings, counts, payload

    engine = coach.app.state.engine
    event.listen(engine, "before_cursor_execute", count_sql)
    try:
        cold_team, team_sql, team = run(MODULE + "/team", n=1)
        row = next(row for row in team["athletes"] if row["name"].endswith("growth-declined"))
        detail = athlete_path(row) + "/measurements?period=90"
        cold_detail, detail_sql, _ = run(detail, n=1)
        for label, path, cold, cold_sql in (
            ("team20", MODULE + "/team", cold_team, team_sql), ("athlete90", detail, cold_detail, detail_sql),
        ):
            times, counts, _ = run(path)
            assert all(item["workouts"] == item["wellness_entries"] == 1 and item["workout_samples"] == 0
                       for item in cold_sql + counts)
            p95 = sorted(times)[ceil(.95 * len(times)) - 1]
            print(f"G5 {label}: n={len(times)} cold_ms={cold[0]:.1f} p95_ms={p95:.1f} "
                  f"max_ms={max(times):.1f} selects={counts[0]['selects']} workload_sql=1 diary_sql=1 raw_samples_sql=0")
    finally:
        event.remove(engine, "before_cursor_execute", count_sql)
