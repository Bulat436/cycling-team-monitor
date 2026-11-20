from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app import analytics
from app.analytics_schemas import DateRange
from app.models import User
from app.onelap_models import OnelapConnection
from app.workout_series import sensor_series
from fit_fixtures import START, cycling_fit
from test_athletes import API, DIARY, edit_profile, team, write
from test_workout_import import upload

CUSTOM = "period=custom&start=2026-01-02&end=2026-01-09"


def get(client, path):
    result = client.get(API + path)
    assert result.status_code == 200, result.text
    return result.json()


def clock(monkeypatch, stamp="2026-01-10T11:00:00"):
    now = datetime.fromisoformat(stamp).replace(tzinfo=UTC).timestamp()
    monkeypatch.setattr(analytics, "time", lambda: now)
    return now


def ftp(coach, csrf, athlete_id, day, watts):
    assert write(coach, "PUT", f"/athletes/{athlete_id}/ftp/{day}", {"watts": watts}, csrf).status_code == 200


def zones(coach, csrf, athlete_id, kind, bands):
    assert write(coach, "PUT", f"/athletes/{athlete_id}/zones/{kind}", {"zones": bands}, csrf).status_code == 200


def import_ride(owner, csrf, **options):
    response = upload(owner, cycling_fit(**options), csrf)
    assert response.status_code == 200, response.text
    return response.json()["workout_id"]


def test_imported_fit_metrics_feed_workouts_weeks_zones_rpe_and_ftp(team, monkeypatch):
    clock(monkeypatch)
    (coach, coach_csrf), (owner, athlete_id, csrf), _ = team
    ftp(coach, coach_csrf, athlete_id, "2025-12-31", 250)
    zones(coach, coach_csrf, athlete_id, "power", [{"name": "Рабочая", "minimum": 200, "maximum": None}])
    zones(coach, coach_csrf, athlete_id, "heart-rate", [{"name": "Тренировка", "minimum": 140, "maximum": 160}])
    first = import_ride(owner, csrf)
    assert write(owner, "PUT", f"/workouts/{first}/rpe", {"score": 7}, csrf).status_code == 200

    ftp(coach, coach_csrf, athlete_id, "2026-01-05", 300)
    zones(coach, coach_csrf, athlete_id, "power", [{"name": "Рабочая", "minimum": 0, "maximum": 200}])
    zero = import_ride(owner, csrf, start=START + 3 * 86400, power=0)
    assert write(owner, "PUT", f"/workouts/{zero}/rpe", {"score": 0}, csrf).status_code == 200
    missing = import_ride(owner, csrf, start=START + 5 * 86400, power=None)

    path = f"/analytics/athletes/{athlete_id}"
    data = get(coach, path + "?" + CUSTOM)
    assert data["period"] == {"start": "2026-01-02", "end": "2026-01-09"}
    assert [item["id"] for item in data["workouts"]] == [first, zero, missing]
    source = get(coach, f"/workouts/{first}")
    first_point, zero_point, missing_point = data["workouts"]
    assert first_point["date"] == "2026-01-02" and first_point["timer_seconds"] == 120
    assert first_point["np"] == source["np"] == pytest.approx(200)
    assert first_point["avg_power"] == source["summary"]["avg_power"] == 200
    assert first_point["tss"] == source["tss"] == pytest.approx(120 / 3600 * (200 / 250) ** 2 * 100)
    assert first_point["distance_m"] == source["summary"]["distance_m"] == 600
    assert zero_point["np"] == zero_point["tss"] == zero_point["rpe"] == zero_point["rpe_load"] == 0
    assert missing_point["np"] is None and missing_point["reasons"]["np"] == "channel_missing"
    assert missing_point["tss"] is None and missing_point["reasons"]["rpe"] == "rpe_missing"
    assert data["load"]["tss_count"] == data["load"]["rpe_load_count"] == 2
    assert [week["workouts"] for week in data["weeks"]] == [1, 2]
    assert [week["rpe_load"] for week in data["weeks"]] == [14, 0]
    assert data["weeks"][1]["tss"] == 0 and data["weeks"][1]["tss_count"] == 1
    assert data["power_zones"]["omitted"] == {"channel_missing": 1}
    assert [(band["minimum"], band["seconds"]) for band in data["power_zones"]["bands"]] == [(0, 120), (200, 120)]
    assert data["heart_rate_zones"]["bands"][0]["seconds"] == 360
    assert data["ftp"] == [
        {"date": "2026-01-02", "watts": 250, "effective_from": "2025-12-31"},
        {"date": "2026-01-05", "watts": 300, "effective_from": "2026-01-05"},
        {"date": "2026-01-09", "watts": 300, "effective_from": "2026-01-05"},
    ]
    assert get(coach, path + "?period=7")["load"]["workouts"] == 2
    assert get(coach, path + "?period=28")["load"]["workouts"] == 3
    assert len(get(coach, path + "?period=90")["wellness"]) == 90
    empty = get(coach, path + "?period=custom&start=2025-12-01&end=2025-12-03")
    assert empty["load"]["tss"] is None and empty["power_zones"]["outside_seconds"] is None
    assert empty["weeks"][0]["timer_seconds"] is None


def test_wellness_baseline_excludes_current_and_keeps_gaps(team, monkeypatch):
    clock(monkeypatch)
    (coach, _), (owner, athlete_id, csrf), _ = team
    base = f"/athletes/{athlete_id}/wellness/"
    for day, score in ((1, 2), (2, 3), (3, 4), (4, 5)):
        assert write(owner, "PUT", base + f"2026-01-0{day}", DIARY | {"sleep_quality": score}, csrf).status_code == 200
    history = get(coach, f"/analytics/athletes/{athlete_id}?period=custom&start=2026-01-03&end=2026-01-05")
    three, four, five = history["wellness"]
    assert three["baseline"] is None
    assert four["baseline"]["sleep_quality"] == 3 and four["scores"]["sleep_quality"] == 5
    assert five["scores"] is None and five["baseline"]["sleep_quality"] == 3.5


def test_team_filters_sorting_and_alert_thresholds(team, monkeypatch):
    current = clock(monkeypatch)
    (coach, coach_csrf), (owner, athlete_id, csrf), (other, other_id, other_csrf) = team
    with Session(coach.app.state.engine) as db:
        for id_ in (athlete_id, other_id):
            db.get(User, id_).activated_at = int(current) - 20 * 86400
        db.add(OnelapConnection(athlete_id=athlete_id, status="temporary_error", history_days=90, secret="fixture"))
        db.commit()
    assert edit_profile(coach, coach_csrf, athlete_id, name="Аня", team_group="Шоссе").status_code == 204
    assert edit_profile(coach, coach_csrf, other_id, name="Борис", team_group="Трек").status_code == 204
    ftp(coach, coach_csrf, athlete_id, "2025-12-31", 250)
    ride = import_ride(owner, csrf, start=int(current) - 3 * 86400)
    assert write(owner, "PUT", f"/athletes/{athlete_id}/wellness/2026-01-10", DIARY | {
        "illness_symptoms": True, "leg_fatigue": 4, "muscle_soreness": 4, "motivation": 1,
    }, csrf).status_code == 200
    target = "/analytics/team"
    items = get(coach, target + "?sort=tss&direction=desc")
    assert items["total"] == 2 and items["filled"] == 1 and items["alerted"] == 2
    assert [row["name"] for row in items["rows"]] == ["Аня", "Борис"]
    a, b = items["rows"]
    assert a["last_workout"]["id"] == ride and a["last_workout"]["rpe"] is None
    assert {alert["code"] for alert in a["alerts"]} == {"fatigue", "illness", "soreness", "low_motivation", "rpe_missing", "sync_error"}
    assert {alert["code"] for alert in b["alerts"]} == {"diary_missing", "inactive_training"}
    assert [row["name"] for row in get(coach, target + "?diary=missing&account=active&warning=inactive_training")["rows"]] == ["Борис"]
    assert [row["name"] for row in get(coach, target + "?search=АНЯ&group=Шоссе&diary=filled")["rows"]] == ["Аня"]
    assert [row["name"] for row in get(coach, target + "?sort=name&direction=desc")["rows"]] == ["Борис", "Аня"]
    assert get(coach, target + "?period=custom&start=2025-12-02&end=2025-12-02")["rows"][0]["load"]["tss"] is None

    settings_path = "/analytics/alert-settings"
    settings = get(coach, settings_path)
    coach_payload = settings | {"enabled": ["fatigue", "diary_missing"], "fatigue_min": 5, "diary_due_hour": 15}
    assert write(other, "PUT", settings_path, coach_payload, other_csrf).status_code == 403
    assert write(coach, "PUT", settings_path, coach_payload).status_code == 403
    assert write(coach, "PUT", settings_path, coach_payload | {"fatigue_min": 6}, coach_csrf).status_code == 422
    assert write(coach, "PUT", settings_path, coach_payload, coach_csrf).status_code == 200
    assert get(coach, settings_path)["fatigue_min"] == 5
    assert get(coach, target + "?warning=any")["rows"] == []


def test_access_ranges_and_sensor_samples(team, monkeypatch):
    clock(monkeypatch)
    (coach, _), (owner, athlete_id, csrf), (other, other_id, _) = team
    workout_id = import_ride(owner, csrf, samples=[0, 1, 2, 7, 8, 9], power=lambda second: None if second == 7 else 200)
    series = f"/workouts/{workout_id}/series"
    assert owner.get(API + series).status_code == 403
    assert other.get(API + series).status_code == 404
    assert owner.get(API + "/analytics/team").status_code == 403
    assert owner.get(API + f"/analytics/athletes/{athlete_id}").status_code == 403
    assert owner.get(API + f"/analytics/athletes/{other_id}/overview").status_code == 404
    assert owner.get(API + "/analytics/alert-settings").status_code == 403
    assert coach.get(API + "/analytics/team?period=custom&start=2026-01-10&end=2026-01-01").status_code == 422
    assert coach.get(API + "/analytics/team?period=custom&start=2023-01-10&end=2026-01-01").status_code == 422
    data = get(coach, series)
    assert data["power"][0] == {"second": 0, "value": 200}
    assert data["heart_rate"][0]["value"] == 150 and data["cadence"][0]["value"] == 88
    assert {"second": 3, "value": None} in data["power"]


def test_downsampling_preserves_peaks_zeros_and_gaps():
    rows = [
        {"timestamp": START + second, "power": 1200 if second == 217 else 0, "heart_rate": None, "cadence": 88}
        for second in range(2000) if not 530 <= second <= 650
    ]
    result = sensor_series(rows, START, 100)
    assert len(result["power"]) < 500
    assert any(point.second == 217 and point.value == 1200 for point in result["power"])
    assert any(point.value == 0 for point in result["power"])
    assert any(point.value is None for point in result["power"])
    assert result["heart_rate"] == []


def test_date_bounds_honor_daylight_saving():
    lower, upper = analytics.bounds(
        DateRange(start=datetime(2026, 3, 8).date(), end=datetime(2026, 3, 8).date()), "America/Los_Angeles",
    )
    assert upper - lower == 23 * 3600
    assert datetime.fromtimestamp(lower, ZoneInfo("America/Los_Angeles")).hour == 0
