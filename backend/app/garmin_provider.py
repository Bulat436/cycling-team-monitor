"""Official, server-side Garmin Connect Personal API transport."""

import base64
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from time import monotonic, sleep, time

import httpx

ORIGIN = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
SLEEP_PATH = "/sleep-service/sleep/dailySleepSummariesByDate"
MAX_DAYS = 31
MAX_SECONDS = 86400
MAX_ID = (1 << 63) - 1
MAX_RESPONSE = 512 * 1024
MAX_TIMESTAMP_MS = 253402214399999


class GarminError(Exception):
    def __init__(self, code: str, retry_at: float | None = None):
        super().__init__(code)
        self.code = code
        self.retry_at = retry_at


def valid_api_key(value: str) -> bool:
    return 0 < len(value) <= 4096 and all(33 <= ord(character) <= 126 for character in value)


def bounded_int(value, maximum: int, minimum: int = 0) -> int | None:
    return value if type(value) is int and minimum <= value <= maximum else None


def iso_datetime(value) -> datetime | None:
    if isinstance(value, str) and 11 < len(value) <= 40 and value[10] == "T":
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return None


def utc_milliseconds(value) -> int | None:
    if type(value) is int:
        return bounded_int(value, MAX_TIMESTAMP_MS)
    timestamp = iso_datetime(value)
    if timestamp is not None:
        # The Garmin GMT field itself identifies UTC when no suffix is sent.
        try:
            return bounded_int(round(timestamp.replace(tzinfo=timestamp.tzinfo or UTC).timestamp() * 1000), MAX_TIMESTAMP_MS)
        except (OverflowError, OSError, ValueError):
            pass
    return None


def offset_minutes(value, utc_ms: int) -> int | None:
    if type(value) is int:
        difference = value - utc_ms
        if difference % 60000 == 0:
            return bounded_int(difference // 60000, 14 * 60, -14 * 60)
        return None
    timestamp = iso_datetime(value)
    if timestamp is not None and timestamp.utcoffset() is not None and utc_milliseconds(value) == utc_ms:
        seconds = int(timestamp.utcoffset().total_seconds())
        if seconds % 60 == 0:
            return bounded_int(seconds // 60, 14 * 60, -14 * 60)
    return None


@dataclass(frozen=True)
class SleepObservation:
    external_id: int | None
    garmin_date: date
    start_ms: int
    end_ms: int
    start_offset_minutes: int | None
    end_offset_minutes: int | None
    sleep_seconds: int
    deep_seconds: int | None
    light_seconds: int | None
    rem_seconds: int | None
    awake_seconds: int | None
    sleep_score: int | None
    average_heart_rate: float | None


@dataclass(frozen=True)
class SleepBatch:
    start: date
    end: date
    account_ids: frozenset[int]
    observations: list[SleepObservation]
    skipped: int


def parse_observation(row: dict, start: date, end: date) -> SleepObservation | None:
    calendar_date = row.get("calendarDate")
    if not isinstance(calendar_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", calendar_date):
        return None
    try:
        garmin_date = date.fromisoformat(calendar_date)
    except ValueError:
        return None
    start_ms = utc_milliseconds(row.get("sleepStartTimestampGMT"))
    end_ms = utc_milliseconds(row.get("sleepEndTimestampGMT"))
    duration = bounded_int(row.get("sleepTimeSeconds"), MAX_SECONDS)
    if (not start <= garmin_date <= end or start_ms is None or end_ms is None or duration is None
            or not 0 < end_ms - start_ms <= MAX_SECONDS * 1000 or duration * 1000 > end_ms - start_ms
            or row.get("sleepWindowConfirmed") is False
            or row.get("sleepWindowConfirmationType") in ("unconfirmed", "off_wrist")):
        return None
    if abs((datetime.fromtimestamp(end_ms / 1000, UTC).date() - garmin_date).days) > 2:
        return None

    scores = row.get("sleepScores")
    overall = scores.get("overall") if isinstance(scores, dict) else None
    score = bounded_int(overall.get("value"), 100) if isinstance(overall, dict) else None
    heart_rate = row.get("avgHeartRate")
    if type(heart_rate) not in (int, float) or not 0 < heart_rate <= 300:
        heart_rate = None
    return SleepObservation(
        bounded_int(row.get("id"), MAX_ID, 1), garmin_date, start_ms, end_ms,
        offset_minutes(row.get("sleepStartTimestampLocal"), start_ms),
        offset_minutes(row.get("sleepEndTimestampLocal"), end_ms), duration,
        bounded_int(row.get("deepSleepSeconds"), MAX_SECONDS), bounded_int(row.get("lightSleepSeconds"), MAX_SECONDS),
        bounded_int(row.get("remSleepSeconds"), MAX_SECONDS), bounded_int(row.get("awakeSleepSeconds"), MAX_SECONDS),
        score, float(heart_rate) if heart_rate is not None else None,
    )


def retry_deadline(header: str | None, now: float) -> float:
    if header:
        header = header.strip()
        if header.isascii() and header.isdecimal():
            digits = header.lstrip("0") or "0"
            return now + max(1, int(digits)) if len(digits) <= 12 else math.inf
        try:
            parsed = parsedate_to_datetime(header)
            return max(now + 1, parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp())
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    return now + 60


class GarminClient:
    def __init__(self, api_key: str, *, reserve, block, transport=None, clock=time, wait=sleep):
        if not valid_api_key(api_key):
            raise GarminError("invalid_key")
        self._api_key = api_key
        self._token: str | None = None
        self._expires_at = 0.0
        self.reserve = reserve
        self.block = block
        self.clock = clock
        self.wait = wait
        self._http = httpx.Client(
            base_url=ORIGIN, trust_env=False, follow_redirects=False, timeout=8.0, transport=transport,
        )

    def close(self):
        self._api_key = ""
        self._token = None
        self._expires_at = 0.0
        self._http.close()

    def _request(self, method: str, path: str, authorization: str, params=None) -> bytes:
        for attempt in range(3):
            self.reserve()
            try:
                return self._read(method, path, authorization, params)
            except GarminError as error:
                if error.code != "unavailable" or attempt == 2:
                    raise
                self.wait(2 ** attempt)
        raise AssertionError("Unreachable request state")

    def _read(self, method, path, authorization, params) -> bytes:
        headers = {"Authorization": authorization, "Accept-Encoding": "identity"}
        deadline = monotonic() + 20
        try:
            with self._http.stream(method, path, headers=headers, params=params) as response:
                status = response.status_code
                if status == 429:
                    until = retry_deadline(response.headers.get("Retry-After"), self.clock())
                    self.block(until)
                    raise GarminError("rate_limited", until)
                if status == 401:
                    raise GarminError("authentication_failed")
                if status == 403 or (path == TOKEN_PATH and status == 400):
                    raise GarminError("access_denied")
                if status in (408, 425) or 500 <= status <= 599:
                    raise GarminError("unavailable")
                if status == 204 and path == SLEEP_PATH:
                    return b"[]"
                if status != 200 or response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise GarminError("invalid_response")
                content = bytearray()
                chunks = (response.content,) if response.is_stream_consumed else response.iter_raw()
                for chunk in chunks:
                    if monotonic() > deadline:
                        raise GarminError("unavailable")
                    if len(content) + len(chunk) > MAX_RESPONSE:
                        raise GarminError("invalid_response")
                    content.extend(chunk)
                return bytes(content)
        except (httpx.RequestError, httpx.InvalidURL):
            raise GarminError("unavailable") from None

    def _access_token(self) -> str:
        now = self.clock()
        if self._token is not None and self._expires_at > now + 5:
            return self._token
        data = self._request("POST", TOKEN_PATH, "ApiKey " + self._api_key)
        now = self.clock()
        try:
            token = data.decode("ascii").strip()
            if len(token) > 16384 or not re.fullmatch(r"[\w-]+\.[\w-]+\.[\w-]+", token, re.ASCII):
                raise ValueError
            payload = token.split(".")[1]
            expiry = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"]
            if type(expiry) not in (int, float) or not now < expiry < math.inf:
                raise ValueError
        except (ValueError, LookupError, TypeError, RecursionError):
            raise GarminError("invalid_response") from None
        # Used only to schedule a new exchange, never as proof of identity.
        self._token = token
        self._expires_at = min(expiry, now + MAX_SECONDS)
        return token

    def _sleep_batch(self, start: date, end: date) -> SleepBatch:
        params = {"startDate": start.isoformat(), "endDate": end.isoformat()}
        token = self._access_token()
        try:
            body = self._request("GET", SLEEP_PATH, "Bearer " + token, params)
        except GarminError as error:
            if error.code != "authentication_failed":
                raise
            self._token = None
            token = self._access_token()
            body = self._request("GET", SLEEP_PATH, "Bearer " + token, params)
        try:
            rows = json.loads(body)
        except (ValueError, RecursionError):
            raise GarminError("invalid_response") from None
        if not isinstance(rows, list) or len(rows) > MAX_DAYS * 16:
            raise GarminError("invalid_response")
        accounts = set()
        observations = []
        for row in rows:
            account = bounded_int(row.get("userProfilePK"), MAX_ID, 1) if isinstance(row, dict) else None
            if account is not None:
                accounts.add(account)
                observation = parse_observation(row, start, end)
                if observation is not None:
                    observations.append(observation)
        return SleepBatch(start, end, frozenset(accounts), observations, len(rows) - len(observations))

    def sleep_batches(self, start: date, end: date):
        if start > end:
            raise GarminError("invalid_range")
        while True:
            last = start + timedelta(days=min(MAX_DAYS - 1, (end - start).days))
            yield self._sleep_batch(start, last)
            if last == end:
                break
            start = last + timedelta(days=1)
