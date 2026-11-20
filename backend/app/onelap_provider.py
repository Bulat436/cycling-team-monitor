"""CTM transport for an operator-approved HTTPS gateway, not the private mobile API."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import monotonic, time
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

JSON_LIMIT = 512 * 1024


class ProviderError(Exception):
    def __init__(self, code: str, retry_at: float | None = None):
        super().__init__(code)
        self.code = code
        self.retry_at = retry_at


class GatewaySession(BaseModel):
    subject: Annotated[str, Field(min_length=1, max_length=256, repr=False)]
    access_token: SecretStr = Field(min_length=1, max_length=4096, repr=False)
    refresh_token: SecretStr | None = Field(default=None, min_length=1, max_length=4096, repr=False)


class RemoteWorkout(BaseModel):
    id: Annotated[str, Field(min_length=1, max_length=512)]
    revision: Annotated[str, Field(min_length=1, max_length=512)]
    started_at: Annotated[int, Field(strict=True, ge=0)]


class WorkoutPage(BaseModel):
    workouts: Annotated[list[RemoteWorkout], Field(max_length=250)]
    cursor: Annotated[str, Field(min_length=1, max_length=1024)] | None = None


def retry_after(value: str | None) -> float:
    current = time()
    if value:
        try:
            if value.isascii() and value.isdecimal() and len(value) < 12:
                seconds = int(value)
            else:
                parsed = parsedate_to_datetime(value)
                seconds = (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp() - current
            return current + min(7 * 86400, max(1, seconds))
        except (ValueError, TypeError, OverflowError):
            pass
    return current + 60


def gateway_origin(value: str) -> str:
    try:
        parsed = httpx.URL(value)
    except httpx.InvalidURL:
        raise ValueError("CTM_ONELAP_GATEWAY must be an HTTPS origin") from None
    if (parsed.scheme != "https" or not parsed.host or parsed.userinfo
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("CTM_ONELAP_GATEWAY must be an HTTPS origin")
    return str(parsed.copy_with(path="/"))


class OnelapFitProvider:
    def __init__(self, origin: str, gate, max_bytes: int, *, transport=None):
        self.origin = gateway_origin(origin)
        self.client = httpx.Client(
            base_url=self.origin, trust_env=False, follow_redirects=False, timeout=8.0, transport=transport,
        )
        self.gate = gate
        self.max_bytes = max_bytes

    def close(self):
        self.client.close()

    def _request(self, method, path, *, session=None, payload=None, limit=JSON_LIMIT):
        self.gate.reserve()
        deadline = monotonic() + 30
        headers = {"Accept-Encoding": "identity"}
        if session is not None:
            try:
                token = session.access_token.get_secret_value()
                if not token.isascii() or any(not 33 <= ord(character) <= 126 for character in token):
                    raise ProviderError("invalid_response")
            except (ValueError, TypeError):
                raise ProviderError("invalid_response") from None
            headers["Authorization"] = "Bearer " + token
        try:
            with self.client.stream(method, path, headers=headers, json=payload) as response:
                status = response.status_code
                if status == 429:
                    until = retry_after(response.headers.get("Retry-After"))
                    self.gate.block(until)
                    raise ProviderError("rate_limited", until)
                if status == 401:
                    raise ProviderError("session_expired")
                if status in (409, 412, 428):
                    raise ProviderError("interaction_required")
                if status in (408, 425) or status >= 500:
                    raise ProviderError("network_error")
                if status == 403:
                    raise ProviderError("provider_denied")
                if status == 404 and path == "/v1/rides/fit":
                    raise ProviderError("fit_unavailable")
                if not 200 <= status < 300 or response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ProviderError("invalid_response")
                buffer = bytearray()
                # MockTransport delivers already-buffered bodies. Live responses are streamed.
                chunks = (response.content,) if response.is_stream_consumed else response.iter_raw()
                for chunk in chunks:
                    if monotonic() > deadline:
                        raise ProviderError("network_error")
                    if len(buffer) + len(chunk) > limit:
                        raise ProviderError("fit_too_large" if path == "/v1/rides/fit" else "invalid_response")
                    buffer.extend(chunk)
                return bytes(buffer)
        except (httpx.RequestError, httpx.InvalidURL, UnicodeError):
            raise ProviderError("network_error") from None

    @staticmethod
    def _parse(model, content):
        try:
            return model.model_validate_json(content)
        except ValidationError:
            raise ProviderError("invalid_response") from None

    def connect(self, login, password):
        try:
            return self._parse(GatewaySession, self._request(
                "POST", "/v1/session", payload={"login": login, "password": password},
            ))
        except ProviderError as exc:
            if exc.code == "session_expired":
                raise ProviderError("invalid_credentials") from None
            raise

    def refresh(self, session):
        if session.refresh_token is None:
            raise ProviderError("reauthorization")
        try:
            replacement = self._parse(GatewaySession, self._request(
                "POST", "/v1/session/refresh", payload={"refresh_token": session.refresh_token.get_secret_value()},
            ))
        except ProviderError as exc:
            if exc.code == "session_expired":
                raise ProviderError("reauthorization") from None
            raise
        if replacement.subject != session.subject:
            raise ProviderError("account_mismatch")
        return replacement

    def disconnect(self, session):
        self._request("DELETE", "/v1/session", session=session)

    def check(self, session):
        self._request("GET", "/v1/session", session=session)

    def list_workouts(self, session, *, started_from, changed_from, through, cursor=None):
        params = {"through": through}
        for key, value in (("started_from", started_from), ("changed_from", changed_from), ("cursor", cursor)):
            if value is not None:
                params[key] = value
        return self._parse(WorkoutPage, self._request("POST", "/v1/rides", session=session, payload=params))

    def workout_metadata(self, session, workout_id):
        item = self._parse(RemoteWorkout, self._request(
            "POST", "/v1/rides/details", session=session, payload={"id": workout_id},
        ))
        if item.id != workout_id:
            raise ProviderError("invalid_response")
        return item

    def download_fit(self, session, item):
        return self._request(
            "POST", "/v1/rides/fit", session=session,
            payload={"id": item.id, "revision": item.revision}, limit=self.max_bytes,
        )
