"""Windows owner-scoped DPAPI storage for the Garmin Personal API key."""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from tempfile import NamedTemporaryFile

from .garmin_provider import GarminError, valid_api_key


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data: bytes, entropy: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise GarminError("windows_required")
    crypto = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    local_free = ctypes.WinDLL("Kernel32.dll", use_last_error=True).LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    extra_buffer = (ctypes.c_ubyte * len(entropy)).from_buffer_copy(entropy)
    input_blob = DataBlob(len(data), buffer)
    extra_blob = DataBlob(len(entropy), extra_buffer)
    output = DataBlob()
    operation = crypto.CryptProtectData if protect else crypto.CryptUnprotectData
    operation.argtypes = [
        ctypes.POINTER(DataBlob), wintypes.LPCWSTR if protect else ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    operation.restype = wintypes.BOOL
    try:
        # CRYPTPROTECT_UI_FORBIDDEN. Machine-wide decryption is deliberately disabled.
        if not operation(ctypes.byref(input_blob), None, ctypes.byref(extra_blob), None, None, 1, ctypes.byref(output)):
            raise GarminError("credential_unavailable")
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(buffer, 0, len(data))
        if output.data:
            if not protect:
                ctypes.memset(output.data, 0, output.size)
            local_free(output.data)


class WindowsKeyVault:
    def __init__(self, path: Path, installation_id: str):
        self.path = path
        self.entropy = bytes.fromhex(installation_id)

    def save(self, key: str):
        if not valid_api_key(key):
            raise GarminError("invalid_key")
        encrypted = dpapi(key.encode("ascii"), self.entropy, protect=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=self.path.parent, delete=False) as file:
                temporary = Path(file.name)
                file.write(encrypted)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(self.path)
        except OSError:
            raise GarminError("credential_unavailable") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(self) -> str:
        try:
            with self.path.open("rb") as file:
                encrypted = file.read(16385)
            if not 0 < len(encrypted) <= 16384:
                raise GarminError("credential_unavailable")
            key = dpapi(encrypted, self.entropy, protect=False).decode("ascii")
            if not valid_api_key(key):
                raise GarminError("credential_unavailable")
            return key
        except (OSError, UnicodeError):
            raise GarminError("credential_unavailable") from None

    def remove(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise GarminError("credential_unavailable") from None
