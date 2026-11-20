from pathlib import Path

import pytest

from app.fit_parser import FitProblem, parse_fit
from app.workout_metrics import calculate
from fit_fixtures import START, cycling_fit, magene_wrapper

LIMIT = 32 * 1024 * 1024
SNAPSHOT = {
    "ftp_watts": 250,
    "power_zones": [
        {"name": "Легко", "minimum": 0, "maximum": 200},
        {"name": "Работа", "minimum": 200, "maximum": None},
    ],
    "heart_rate_zones": [{"name": "Работа", "minimum": 120, "maximum": 160}],
}


def project(data, snapshot=SNAPSHOT):
    return calculate(parse_fit(data, max_bytes=LIMIT), snapshot)


@pytest.mark.parametrize("options", [
    {},
    {"header_size": 12, "endian": ">", "manufacturer": 1},
    {"reserved": True},
    {"compressed": True},
])
def test_decodes_profiles_variants_and_preserves_records(options):
    data = cycling_fit(**options)
    activity = parse_fit(data, max_bytes=LIMIT)
    assert len(activity.samples) == 120
    assert [row["timestamp"] for row in activity.samples] == list(range(START, START + 120))
    first = activity.samples[0]
    assert (first["power"], first["heart_rate"], first["cadence"], first["speed"], first["altitude"]) == (200, 150, 88, 5, 100)
    assert (first["latitude"], first["longitude"], first["temperature"]) == (0, 90, -3)
    assert any(item["field"] == 200 and item["value"] == 42 for item in first["fields"])
    projection = calculate(activity, SNAPSHOT)
    assert projection.timer_seconds == 120
    assert projection.summary["distance_m"] == 600
    assert projection.summary["ascent_m"] == 29
    assert projection.summary["descent_m"] == 12
    assert projection.summary["moving_seconds"] == 120
    assert projection.metrics["np"] == pytest.approx(200)
    assert projection.metrics["if_value"] == pytest.approx(.8)
    assert projection.metrics["tss"] == pytest.approx(120 / 3600 * .8 ** 2 * 100)


def test_native_reference_fixture_matches_generator():
    assert Path(__file__).with_name("fixtures").joinpath("cycling-power.fit").read_bytes() == cycling_fit()


def test_magene_round_trip_and_expansion_limit():
    source = cycling_fit()
    original = parse_fit(source, max_bytes=LIMIT)
    wrapped = magene_wrapper(source)
    decoded = parse_fit(wrapped, max_bytes=LIMIT)
    assert decoded.container == "magene_zip"
    assert decoded.samples == original.samples
    assert decoded.fit_hash == original.fit_hash
    assert len(wrapped) < len(source)
    with pytest.raises(FitProblem, match="fit_too_large"):
        parse_fit(wrapped, max_bytes=len(wrapped))
    with pytest.raises(FitProblem, match="fit_invalid_structure"):
        parse_fit(wrapped[:-4], max_bytes=LIMIT)


def test_variable_power_np_uses_fourth_power_of_rolling_means():
    workout = project(cycling_fit(elapsed=60, power=lambda second: 100 if second < 30 else 300))
    # In this fixture there are 31 complete windows. Each new window replaces
    # one 100 W sample with one 300 W sample; compute their means independently.
    expected = (sum((100 + step * (200 / 30)) ** 4 for step in range(31)) / 31) ** .25
    assert workout.metrics["np"] == pytest.approx(expected, abs=1e-10)
    assert workout.summary["avg_power"] == 200
    assert workout.metrics["quality"]["np_windows"] == 31


def test_pauses_do_not_mix_windows_and_zero_is_measured():
    def watts(second):
        if second < 60:
            return 0
        return 4000 if second < 120 else 200

    workout = project(cycling_fit(
        elapsed=180, ranges=[(0, 60), (120, 180)], power=watts,
        speed=lambda second: 0 if second < 30 else 5,
    ))
    assert workout.summary["elapsed_seconds"] == 180
    assert workout.timer_seconds == 120
    assert workout.summary["moving_seconds"] == 90
    assert workout.summary["max_power"] == 200
    assert workout.metrics["np"] == pytest.approx((200 ** 4 / 2) ** .25)
    assert workout.metrics["quality"]["np_windows"] == 62
    assert [row["seconds"] for row in workout.metrics["power_zones"]["zones"]] == [60, 60]
    assert workout.metrics["heart_rate_zones"]["zones"][0]["seconds"] == 120
    assert [row["minimum"] for row in workout.metrics["power_distribution"]] == [0, 200]


def test_gap_based_pauses_require_matching_device_timer():
    recorded = [*range(60), *range(120, 180)]
    base = {"elapsed": 180, "samples": recorded, "include_events": False}
    corroborated = project(cycling_fit(**base, timer=120))
    assert corroborated.metrics["quality"]["timing_source"] == "record_gaps_verified"
    assert corroborated.metrics["np"] == pytest.approx(200)
    ambiguous = project(cycling_fit(**base, timer=150))
    assert ambiguous.metrics["np"] is None
    assert ambiguous.metrics["reasons"]["np"] == "timer_unresolved"
    assert ambiguous.metrics["power_zones"] is None
    assert ambiguous.summary["moving_seconds"] is None


def test_partial_channels_do_not_create_fake_load_or_zone_time():
    damaged_data = cycling_fit(power=lambda second: 200 if second < 55 else None, speed=lambda i: 5 if i < 60 else None)
    workout = project(damaged_data)
    assert workout.metrics["np"] is None
    assert workout.metrics["tss"] is None
    assert workout.summary["avg_power"] is None
    assert workout.summary["moving_seconds"] is None
    assert workout.metrics["reasons"]["np"] == "insufficient_power_coverage"
    zones = workout.metrics["power_zones"]
    assert zones["zones"][1]["seconds"] == 55
    assert zones["unknown_seconds"] == 65
    assert workout.metrics["heart_rate_zones"]["unknown_seconds"] == 0


def test_coverage_of_windows_prevents_cherry_picking():
    workout = project(cycling_fit(elapsed=240, power=lambda i: None if i in (55, 115, 175) else 300))
    assert workout.metrics["quality"]["covered_seconds"]["power"] == 237
    assert workout.metrics["np"] is None
    assert workout.metrics["reasons"]["np"] == "insufficient_power_coverage"


def test_sparse_sampling_never_fills_a_long_gap():
    workout = project(cycling_fit(samples=[0, *range(2, 42), *range(60, 120)]))
    assert workout.metrics["quality"]["covered_seconds"]["power"] == 102
    assert workout.metrics["power_zones"]["unknown_seconds"] == 18
    assert workout.metrics["np"] is None


def test_no_power_short_power_and_no_ftp_are_distinct():
    without_power = project(cycling_fit(power=None))
    assert without_power.metrics["np"] is None
    assert without_power.metrics["reasons"]["np"] == "channel_missing"
    assert without_power.metrics["power_distribution"] is None
    assert without_power.summary["max_power"] is None
    assert without_power.summary["avg_heart_rate"] == 150
    short = project(cycling_fit(elapsed=29))
    assert short.metrics["np"] is None and short.metrics["reasons"]["np"] == "short_power_windows"
    threshold = project(cycling_fit(elapsed=30))
    assert threshold.metrics["np"] == 200 and threshold.metrics["quality"]["np_windows"] == 1
    no_ftp = project(cycling_fit(), SNAPSHOT | {"ftp_watts": None})
    assert no_ftp.metrics["np"] == 200
    assert no_ftp.metrics["if_value"] is None and no_ftp.metrics["tss"] is None
    assert no_ftp.metrics["reasons"]["tss"] == "ftp_missing"
    coasting = project(cycling_fit(power=0))
    assert coasting.summary["avg_power"] == 0 and coasting.metrics["np"] == 0


@pytest.mark.parametrize(("rewrite", "code"), [
    (lambda data: b"fake data", "fit_invalid_signature"),
    (lambda data: data[:-3], "fit_invalid_structure"),
    (lambda data: data[:-1] + bytes([data[-1] ^ 1]), "fit_bad_crc"),
    (lambda data: cycling_fit(file_type=5), "fit_not_activity"),
    (lambda data: data + data, "fit_invalid_structure"),
])
def test_rejects_corrupt_and_other_types(rewrite, code):
    with pytest.raises(FitProblem, match=code):
        parse_fit(rewrite(cycling_fit()), max_bytes=LIMIT)
