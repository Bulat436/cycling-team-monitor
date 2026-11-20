"""Decode and retain FIT activity messages without using vendor-specific profiles as a gate."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from math import isfinite
from struct import error as StructError
from zlib import decompressobj, error as ZlibError

import fitdecode
from fitdecode.types import DevFieldDefinition

MAX_SAMPLES = 250_000
MAX_MESSAGES = 400_000
MAX_RETAINED_FIELDS = 1_500_000
MAX_ACTIVITY_SECONDS = 7 * 86400
SEMICIRCLES_TO_DEGREES = 180 / (1 << 31)


class FitProblem(Exception):
    def __init__(self, code: str, status: int = 422):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass
class Activity:
    started_at: int
    finished_at: int | None
    samples: list[dict]
    sessions: list[dict]
    events: list[dict]
    metadata: dict
    fit_hash: str
    container: str


def _fit_header(data: bytes | bytearray) -> tuple[int, int]:
    if len(data) < 14 or data[8:12] != b".FIT":
        raise FitProblem("fit_invalid_signature")
    if data[0] not in {12, 14}:
        raise FitProblem("fit_invalid_structure")
    return data[0], int.from_bytes(data[4:8], "little")


def _unwrap(data: bytes | bytearray, max_bytes: int) -> tuple[bytes | bytearray, str]:
    if len(data) > max_bytes:
        raise FitProblem("fit_too_large", 413)
    if not data.startswith(b".ZIP"):
        header_size, body_size = _fit_header(data)
        if len(data) != header_size + body_size + 2:
            raise FitProblem("fit_invalid_structure")
        return data, "fit"

    # Some Magene/Geoid firmware wraps the FIT payload in length-prefixed zlib blocks.
    wrapped = memoryview(data)[4:]
    header_size, body_size = _fit_header(bytes(wrapped[:14]))
    expected = header_size + body_size
    if expected + 2 > max_bytes:
        raise FitProblem("fit_too_large", 413)
    output = bytearray(wrapped[:header_size])
    cursor = header_size
    while len(output) < expected:
        if cursor + 2 > len(wrapped) - 2:
            raise FitProblem("fit_invalid_structure")
        size = int.from_bytes(wrapped[cursor:cursor + 2], "little")
        cursor += 2
        end = cursor + size
        if not size or end > len(wrapped) - 2:
            raise FitProblem("fit_invalid_structure")
        inflater = decompressobj()
        remaining = expected - len(output)
        try:
            block = inflater.decompress(wrapped[cursor:end], remaining + 1)
        except ZlibError as exc:
            raise FitProblem("fit_invalid_structure") from exc
        if not block or len(block) > remaining or not inflater.eof or inflater.unused_data:
            raise FitProblem("fit_invalid_structure")
        output.extend(block)
        cursor = end
    if cursor + 2 != len(wrapped):
        raise FitProblem("fit_invalid_structure")
    output.extend(wrapped[cursor:])
    return output, "magene_zip"


def _timestamp(value) -> int | None:
    if not isinstance(value, datetime):
        return None
    timestamp = int((value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp())
    return timestamp if timestamp >= 631065600 else None


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and not isfinite(value):
        return None
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, bytes):
        return {"hex": value.hex()}
    return value


def _native(message):
    return {field.name: field.value for field in message.fields if not isinstance(field.field_def, DevFieldDefinition)}


def _fields(message):
    fields = []
    for field in message.fields:
        item = {"field": field.def_num, "name": field.name, "value": _json_value(field.value)}
        if field.units:
            item["units"] = field.units
        if field.raw_value != field.value:
            item["raw"] = _json_value(field.raw_value)
        if isinstance(field.field_def, DevFieldDefinition):
            item["developer"] = field.field_def.dev_data_index
        fields.append(item)
    return fields


def bounded(value, low: float, high: float) -> float | None:
    if type(value) not in (int, float) or not isfinite(value) or not low <= value <= high:
        return None
    return value


def preferred(values: dict, names: tuple[str, ...], low: float, high: float) -> float | None:
    return next((value for name in names if (value := bounded(values.get(name), low, high)) is not None), None)


CHANNELS = {
    "power": (("power",), 0, 5000),
    "heart_rate": (("heart_rate",), 1, 255),
    "cadence": (("cadence",), 0, 254),
    "speed": (("enhanced_speed", "speed"), 0, 60),
    "altitude": (("enhanced_altitude", "altitude"), -1000, 12000),
    "distance": (("distance",), 0, 10_000_000),
    "temperature": (("temperature",), -80, 80),
}


def _sample(message) -> dict:
    values = _native(message)
    lat, lon = (bounded(values.get(key), -(1 << 31), (1 << 31) - 1) for key in ("position_lat", "position_long"))
    return {
        "timestamp": _timestamp(values.get("timestamp")),
        **{key: preferred(values, names, low, high) for key, (names, low, high) in CHANNELS.items()},
        "latitude": lat * SEMICIRCLES_TO_DEGREES if lat is not None and abs(lat) <= (1 << 30) else None,
        "longitude": lon * SEMICIRCLES_TO_DEGREES if lon is not None else None,
        "fields": _fields(message),
    }


def _session(message) -> dict:
    values = _native(message)
    result = {key: _json_value(value) for key, value in values.items()}
    result["start_time"] = _timestamp(values.get("start_time"))
    result["timestamp"] = _timestamp(values.get("timestamp"))
    return result


def parse_fit(data: bytes | bytearray, *, max_bytes: int) -> Activity:
    decoded, container = _unwrap(data, max_bytes)
    samples, sessions, events = [], [], []
    retained_fields = 0
    metadata: dict = {"file_id": {}, "laps": [], "sessions": []}
    try:
        with fitdecode.FitReader(
            bytes(decoded), check_crc=fitdecode.CrcCheck.RAISE, error_handling=fitdecode.ErrorHandling.RAISE,
        ) as reader:
            for index, frame in enumerate(reader):
                if index >= MAX_MESSAGES:
                    raise FitProblem("fit_too_complex")
                if isinstance(frame, fitdecode.FitHeader):
                    metadata["profile_version"] = ".".join(map(str, frame.profile_ver))
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name in {"record", "session", "lap"}:
                    retained_fields += len(frame.fields)
                    if retained_fields > MAX_RETAINED_FIELDS:
                        raise FitProblem("fit_too_complex")
                if frame.name == "file_id":
                    values = _native(frame)
                    if values.get("type") not in (None, "activity", 4):
                        raise FitProblem("fit_not_activity")
                    metadata["file_id"] = {
                        key: _json_value(values[key]) for key in ("type", "manufacturer", "product", "time_created")
                        if key in values
                    }
                elif frame.name == "record":
                    if len(samples) >= MAX_SAMPLES:
                        raise FitProblem("fit_too_complex")
                    samples.append(_sample(frame))
                elif frame.name == "session":
                    sessions.append(_session(frame))
                    metadata["sessions"].append(_fields(frame))
                elif frame.name == "lap":
                    metadata["laps"].append(_fields(frame))
                elif frame.name == "event":
                    values = _native(frame)
                    timestamp = _timestamp(values.get("timestamp"))
                    if values.get("event") == "timer" and timestamp is not None:
                        events.append({
                            "timestamp": timestamp, "event_type": values.get("event_type"),
                            "group": values.get("event_group"),
                        })
    except fitdecode.FitCRCError as exc:
        raise FitProblem("fit_bad_crc") from exc
    except (fitdecode.FitError, StructError, IndexError, KeyError, ValueError, TypeError, ArithmeticError, AssertionError) as exc:
        raise FitProblem("fit_invalid_structure") from exc

    if not sessions and metadata["file_id"].get("type") not in ("activity", 4):
        raise FitProblem("fit_not_activity")
    sample_times = [row["timestamp"] for row in samples if row["timestamp"] is not None]
    session_starts = [row["start_time"] for row in sessions if row["start_time"] is not None]
    if not session_starts and not sample_times:
        raise FitProblem("fit_no_start")
    started = min(session_starts or sample_times)
    ends = [row["timestamp"] for row in sessions if row["timestamp"] is not None and row["timestamp"] > started]
    finished = max(ends) if ends else max((timestamp for timestamp in sample_times if timestamp >= started), default=None)
    if finished is not None and finished - started > MAX_ACTIVITY_SECONDS:
        raise FitProblem("fit_too_complex")
    return Activity(started, finished, samples, sessions, events, metadata, sha256(decoded).hexdigest(), container)
