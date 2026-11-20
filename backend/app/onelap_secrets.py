import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .onelap_provider import GatewaySession, ProviderError


class SessionVault:
    def __init__(self, keys, audience: str):
        self.audience = audience
        try:
            self.cipher = MultiFernet([Fernet(key.get_secret_value().encode("ascii")) for key in keys]) if keys else None
        except (ValueError, UnicodeError):
            self.cipher = None

    def seal(self, athlete_id: int, generation: int, session: GatewaySession) -> str:
        if self.cipher is None:
            raise ProviderError("key_unavailable")
        packet = {
            "athlete_id": athlete_id, "generation": generation, "subject": session.subject,
            "audience": self.audience,
            "access_token": session.access_token.get_secret_value(),
            "refresh_token": session.refresh_token.get_secret_value() if session.refresh_token else None,
        }
        return self.cipher.encrypt(json.dumps(packet).encode()).decode("ascii")

    def open(self, athlete_id: int, generation: int, ciphertext: str) -> GatewaySession:
        if self.cipher is None:
            raise ProviderError("key_unavailable")
        try:
            packet = json.loads(self.cipher.decrypt(ciphertext.encode("ascii")))
            if (packet.pop("athlete_id") != athlete_id or packet.pop("generation") != generation
                    or packet.pop("audience") != self.audience):
                raise ValueError("Wrong connection")
            return GatewaySession.model_validate(packet)
        except (InvalidToken, ValueError, TypeError, KeyError, AttributeError):
            raise ProviderError("key_unavailable") from None
