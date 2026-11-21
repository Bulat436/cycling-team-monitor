from collections.abc import Iterable
from itertools import pairwise
from math import fsum

POWER_PROJECTION_VERSION = "power-segments-v1"
BEST_SECONDS = (5, 60, 300, 1200)

# Each row covers [timestamp, timestamp + seconds) in one active timer segment.
Observation = tuple[int, dict, int, bool]


def best_power(observations: Iterable[Observation], *, enabled: bool = True) -> dict[str, float | None]:
    best = dict.fromkeys(BEST_SECONDS)
    if not enabled:
        return {str(seconds): value for seconds, value in best.items()}
    capacity = max(BEST_SECONDS) + 1
    totals = [0.0] * capacity
    count = 0
    for _, row, seconds, new_segment in observations:
        power = row.get("power")
        if new_segment or power is None:
            count = 0
            totals[0] = 0
        if power is None:
            continue
        for _ in range(seconds):
            count += 1
            total = totals[(count - 1) % capacity] + power
            totals[count % capacity] = total
            for duration in BEST_SECONDS:
                if count >= duration:
                    value = (total - totals[(count - duration) % capacity]) / duration
                    if best[duration] is None or value > best[duration]:
                        best[duration] = value
    return {str(seconds): value for seconds, value in best.items()}


def interval_power(observations: Iterable[Observation], start: int, duration: int) -> float | None:
    end = start + duration
    covered = []
    for timestamp, row, seconds, new_segment in observations:
        left, right = max(start, timestamp), min(end, timestamp + seconds)
        if timestamp >= end:
            break
        if left >= right:
            continue
        if row.get("power") is None or (new_segment and left > start):
            return None
        covered.append((left, right, row["power"]))
    if (not covered or covered[0][0] != start or covered[-1][1] != end
            or any(left[1] != right[0] for left, right in pairwise(covered))):
        return None
    return fsum((right - left) * power for left, right, power in covered) / duration
