"""Workout projection and versioned calculations from measured FIT records."""

from collections import defaultdict, deque
from dataclasses import dataclass
from math import fsum

from .fit_parser import Activity, CHANNELS, MAX_ACTIVITY_SECONDS, bounded, preferred

VERSION = "fit-cycling-v1"
WINDOW = 30
MAX_HOLD = 2
MIN_COVERAGE = 0.90
MIN_WINDOW_COVERAGE = 0.80
POWER_BIN_WATTS = 50
MOVING_SPEED = 0.5
GAP_TIMING_TOLERANCE_RATIO = 0.005
GAP_TIMING_TOLERANCE_CAP = 15

PARAMETERS = {
    "rolling_window_seconds": WINDOW,
    "max_hold_seconds": MAX_HOLD,
    "min_channel_coverage": MIN_COVERAGE,
    "min_np_window_coverage": MIN_WINDOW_COVERAGE,
    "power_bin_watts": POWER_BIN_WATTS,
    "moving_speed_mps": MOVING_SPEED,
    "gap_timing_tolerance_ratio": GAP_TIMING_TOLERANCE_RATIO,
    "gap_timing_tolerance_max_seconds": GAP_TIMING_TOLERANCE_CAP,
}


@dataclass
class Projection:
    timer_seconds: float | None
    summary: dict
    metrics: dict


def _session_total(activity: Activity, names: tuple[str, ...], maximum: float) -> float | None:
    values = [preferred(session, names, 0, maximum) for session in activity.sessions]
    return fsum(values) if values and all(value is not None for value in values) else None


def _bounds(activity: Activity, elapsed: float | None) -> tuple[int, int] | None:
    started = activity.started_at
    if len(activity.sessions) == 1 and elapsed is not None:
        end = started + round(elapsed)
    else:
        end = activity.finished_at
    return (started, end) if end is not None and end > started else None


def _timer_events(activity: Activity, bounds: tuple[int, int] | None, timer: float | None) -> list[tuple[int, int]]:
    starts = {"start", 0}
    stops = {"stop", "stop_all", "stop_disable", "stop_disable_all", 1, 4, 8, 9}
    events = [event for event in activity.events if event["event_type"] in starts | stops]
    main = [event for event in events if event["group"] in (0, None)]
    if main:
        events = main
    elif len({event["group"] for event in events}) > 1:
        return []

    ranges = []
    start = None
    for index, event in enumerate(sorted(events, key=lambda row: row["timestamp"])):
        timestamp = event["timestamp"]
        if event["event_type"] in starts:
            if start is None:
                start = timestamp
        else:
            if index == 0 and timer is not None and bounds:
                start = bounds[0]
            if start is not None and timestamp > start:
                ranges.append((start, timestamp))
            start = None
    if start is not None:
        if timer is None or not bounds:
            return []
        if bounds[1] > start:
            ranges.append((start, bounds[1]))
    if bounds:
        low, high = bounds
        ranges = [(max(a, low), min(b, high)) for a, b in ranges if min(b, high) > max(a, low)]
    return ranges


def _recorded_ranges(samples, bounds: tuple[int, int]) -> list[tuple[int, int]]:
    timestamps = [timestamp for timestamp, _ in samples if bounds[0] <= timestamp < bounds[1]]
    if not timestamps:
        return []
    ranges = []
    start = previous = timestamps[0]
    for timestamp in timestamps[1:]:
        if timestamp - previous > MAX_HOLD:
            ranges.append((start, previous + 1))
            start = timestamp
        previous = timestamp
    ranges.append((start, previous + 1))
    return ranges


def _active_ranges(activity: Activity, samples, bounds, timer: float | None) -> tuple[list[tuple[int, int]], str]:
    ranges = _timer_events(activity, bounds, timer)
    seconds = sum(end - start for start, end in ranges)
    if ranges and seconds <= MAX_ACTIVITY_SECONDS and (timer is None or abs(seconds - timer) <= 2):
        return ranges, "timer_events"

    if bounds and timer is not None and timer > 0:
        # If the summary shows no detectable pause, gaps are unknown data instead of breaks.
        if abs(bounds[1] - bounds[0] - timer) <= 1:
            return [bounds], "session_timer"
        # Some devices omit events entirely. Only use observed runs when their duration
        # independently agrees with the device timer, within quantization tolerance.
        ranges = _recorded_ranges(samples, bounds)
        seconds = sum(end - start for start, end in ranges)
        tolerance = max(2, min(GAP_TIMING_TOLERANCE_CAP, timer * GAP_TIMING_TOLERANCE_RATIO))
        if ranges and abs(seconds - timer) <= tolerance:
            return ranges, "record_gaps_verified"
    return [], "timer_unresolved"


def _unique_samples(samples: list[dict]) -> list[tuple[int, dict]]:
    merged: dict[int, dict] = {}
    for row in samples:
        timestamp = row["timestamp"]
        if timestamp is not None:
            target = merged.setdefault(timestamp, {})
            target.update({key: row[key] for key in CHANNELS if row[key] is not None})
    return sorted(merged.items())


def _active_samples(samples, ranges):
    """Yield observed whole seconds, split across pauses, long gaps and missing samples."""
    index = 0
    for start, end in ranges:
        previous_end = None
        while index < len(samples) and samples[index][0] < start:
            index += 1
        while index < len(samples):
            timestamp, row = samples[index]
            if timestamp >= end:
                break
            upcoming = samples[index + 1][0] if index + 1 < len(samples) else timestamp + 1
            length = min(upcoming, end) - timestamp
            if upcoming - timestamp > MAX_HOLD:
                length = 1
            yield row, length, timestamp != previous_end
            previous_end = timestamp + length
            index += 1


def _zone_summary(histogram: dict, zones: list[dict], expected: int, samples_present: bool) -> tuple[dict | None, str | None]:
    if not samples_present:
        return None, "channel_missing"
    if not zones:
        return None, "zones_not_configured"
    if not expected:
        return None, "timer_unresolved"
    if not histogram:
        return None, "no_active_samples"
    durations = [0] * len(zones)
    outside = 0
    for value, seconds in histogram.items():
        index = next((i for i, z in enumerate(zones) if value >= z["minimum"] and (
            z["maximum"] is None or value < z["maximum"]
        )), None)
        if index is None:
            outside += seconds
        else:
            durations[index] += seconds
    return {
        "zones": [{**zone, "seconds": duration} for zone, duration in zip(zones, durations, strict=True)],
        "outside_seconds": outside,
        "unknown_seconds": expected - sum(histogram.values()),
    }, None


def _summary_stat(activity: Activity, names: tuple[str, ...], low: float, high: float, maximum: bool):
    values = [preferred(session, names, low, high) for session in activity.sessions]
    if not values or any(value is None for value in values):
        return None
    if maximum:
        return max(values)
    if len(values) == 1:
        return values[0]
    weights = [bounded(session.get("total_timer_time"), 0, MAX_ACTIVITY_SECONDS) for session in activity.sessions]
    if any(weight is None for weight in weights) or not (total := sum(weights)):
        return None
    return fsum(value * weight for value, weight in zip(values, weights, strict=True)) / total


def calculate(activity: Activity, snapshot: dict) -> Projection:
    # Elapsed time is wall time. Only device-reported timer time is used for TSS and session-RPE.
    reported_elapsed = _session_total(activity, ("total_elapsed_time",), MAX_ACTIVITY_SECONDS)
    elapsed = reported_elapsed if len(activity.sessions) == 1 else (
        activity.finished_at - activity.started_at if activity.finished_at is not None else None
    )
    if elapsed is None and activity.finished_at is not None:
        elapsed = activity.finished_at - activity.started_at
    timer = _session_total(activity, ("total_timer_time",), MAX_ACTIVITY_SECONDS)
    if timer is not None and (timer > MAX_ACTIVITY_SECONDS or (elapsed is not None and timer > elapsed + 1)):
        timer = None
    bounds = _bounds(activity, elapsed)
    samples = _unique_samples(activity.samples)
    ranges, timing_source = _active_ranges(activity, samples, bounds, timer)
    expected = sum(end - start for start, end in ranges)
    possible_windows = sum(max(0, end - start - WINDOW + 1) for start, end in ranges)
    available = {key: any(row[key] is not None for row in activity.samples) for key in CHANNELS}
    available["gps"] = any(row["latitude"] is not None and row["longitude"] is not None for row in activity.samples)

    histograms: dict[str, dict[float, int]] = {key: defaultdict(int) for key in CHANNELS}
    window: deque[float] = deque()
    window_total = 0.0
    fourth_powers, window_count = 0.0, 0
    for row, length, new_segment in _active_samples(samples, ranges):
        for key, value in row.items():
            histograms[key][value] += length
        power = row.get("power")
        if new_segment or power is None:
            window.clear()
            window_total = 0
        if power is not None:
            for _ in range(length):
                if len(window) == WINDOW:
                    window_total -= window.popleft()
                window.append(power)
                window_total += power
                if len(window) == WINDOW:
                    fourth_powers += (window_total / WINDOW) ** 4
                    window_count += 1

    coverage = {key: sum(histogram.values()) for key, histogram in histograms.items()}
    reasons = {}
    for key in ("power", "heart_rate"):
        if not available[key]:
            reasons[key] = "channel_missing"
    np = intensity = tss = None
    sports = {row["sport"] for row in activity.sessions if row.get("sport") not in (None, "generic")}
    if sports and sports != {"cycling"}:
        reasons["np"] = "non_cycling_activity"
    elif not available["power"]:
        reasons["np"] = "channel_missing"
    elif not expected:
        reasons["np"] = "timer_unresolved"
    elif not possible_windows:
        reasons["np"] = "short_power_windows"
    elif coverage["power"] / expected < MIN_COVERAGE or window_count / possible_windows < MIN_WINDOW_COVERAGE:
        reasons["np"] = "insufficient_power_coverage"
    else:
        np = (fourth_powers / window_count) ** 0.25
    ftp = snapshot["ftp_watts"]
    if np is None:
        reasons["if"] = reasons["tss"] = reasons["np"]
    elif ftp is None:
        reasons["if"] = reasons["tss"] = "ftp_missing"
    else:
        intensity = np / ftp
        if not timer:
            reasons["tss"] = "timer_missing"
        else:
            tss = timer / 3600 * intensity ** 2 * 100

    zone_summaries = {}
    for channel in ("power", "heart_rate"):
        key = channel + "_zones"
        summary, reason = _zone_summary(histograms[channel], snapshot[key], expected, available[channel])
        zone_summaries[key] = summary
        if reason:
            reasons[key] = reason

    distribution = defaultdict(int)
    for watts, seconds in histograms["power"].items():
        distribution[int(watts // POWER_BIN_WATTS) * POWER_BIN_WATTS] += seconds
    if not distribution:
        reasons["power_distribution"] = "timer_unresolved" if available["power"] and not expected else (
            "no_active_samples" if available["power"] else "channel_missing"
        )
    moving = _session_total(activity, ("total_moving_time",), MAX_ACTIVITY_SECONDS)
    if moving is not None and ((timer is not None and moving > timer + 1) or (
        elapsed is not None and moving > elapsed + 1
    )):
        moving = None
    if moving is None and expected and coverage["speed"] == expected:
        moving = sum(seconds for value, seconds in histograms["speed"].items() if value > MOVING_SPEED)
    if moving is None:
        reasons["moving_seconds"] = "speed_coverage_missing"

    distance = _session_total(activity, ("total_distance",), 10_000_000)
    if distance is None and bounds:
        distances = [(timestamp, row["distance"]) for timestamp, row in samples if "distance" in row]
        if distances and distances[-1][0] >= bounds[1] - MAX_HOLD and all(
            current[1] >= previous[1] for previous, current in zip(distances, distances[1:])
        ):
            distance = distances[-1][1]

    summary = {
        "elapsed_seconds": elapsed, "moving_seconds": moving, "distance_m": distance,
        "ascent_m": _session_total(activity, ("total_ascent",), 100_000),
        "descent_m": _session_total(activity, ("total_descent",), 100_000),
        "calories": _session_total(activity, ("total_calories",), 100_000),
        "gps": available["gps"], "sample_count": len(activity.samples),
        "sports": sorted(sports),
    }
    for channel in ("speed", "power", "heart_rate", "cadence", "temperature"):
        _, low, high = CHANNELS[channel]
        for prefix in ("avg", "max"):
            native = (prefix + "_enhanced_speed", prefix + "_speed") if channel == "speed" else (prefix + "_" + channel,)
            value = _summary_stat(activity, native, low, high, prefix == "max")
            # Some devices export a default 0 for an absent power/cadence sensor.
            if value == 0 and channel in {"power", "cadence"} and not available[channel]:
                value = None
            observed = histograms[channel]
            measured = coverage[channel]
            if value is None and expected and measured / expected >= MIN_COVERAGE:
                value = max(observed) if prefix == "max" else fsum(v * s for v, s in observed.items()) / measured
            summary[prefix + "_" + channel] = value

    altitude = [sample["altitude"] for sample in activity.samples if sample["altitude"] is not None]
    summary["min_altitude_m"] = min(altitude, default=None)
    summary["max_altitude_m"] = max(altitude, default=None)

    return Projection(
        timer_seconds=timer,
        summary=summary,
        metrics={
            "np": np, "if_value": intensity, "tss": tss, **zone_summaries,
            "power_distribution": (
                [{"minimum": start, "maximum": start + POWER_BIN_WATTS, "seconds": seconds}
                 for start, seconds in sorted(distribution.items())] if distribution else None
            ),
            "quality": {
                "timing_source": timing_source, "expected_seconds": expected or None,
                "covered_seconds": coverage, "np_windows": window_count,
                "possible_np_windows": possible_windows, "missing_channels": [key for key, value in available.items() if not value],
            },
            "reasons": reasons,
        },
    )
