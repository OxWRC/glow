"""OS-keychain-backed storage for AWS credentials, with a graceful in-memory fallback.

Real risk area: headless/minimal Linux desktops without a Secret Service
provider (no gnome-keyring/kwallet) can't back `keyring` at all. Rather than
building custom encryption for that case, we degrade to storing credentials
in-memory only for the lifetime of the process, with a clear one-time warning
so the caller (GUI) can surface it via `backend_name()`.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import asdict, dataclass

import keyring
import keyring.errors

_SERVICE_NAME = "glow-deploy"
_KEYRING_UNAVAILABLE_ERRORS = (
    keyring.errors.NoKeyringError,
    keyring.errors.KeyringLocked,
)

_memory_store: dict[str, str] = {}
_keyring_backend_failed = False


@dataclass
class StoredCredentials:
    access_key: str
    secret_key: str
    session_token: str | None
    expiration: str | None  # ISO 8601, if known (SSO role credentials expire)
    region: str


def backend_name() -> str:
    """'keyring' if the OS keychain is usable, else 'memory' (in-process only, lost on exit)."""
    return "memory" if _keyring_backend_failed else "keyring"


def _fall_back_to_memory() -> None:
    global _keyring_backend_failed
    if not _keyring_backend_failed:
        _keyring_backend_failed = True
        warnings.warn(
            "No usable OS keychain found; credentials will only be kept in "
            "memory for this session and must be re-entered next time.",
            RuntimeWarning,
            stacklevel=3,
        )


def save_credentials(profile_name: str, credentials: StoredCredentials) -> None:
    payload = json.dumps(asdict(credentials))
    if not _keyring_backend_failed:
        try:
            keyring.set_password(_SERVICE_NAME, profile_name, payload)
            return
        except _KEYRING_UNAVAILABLE_ERRORS:
            _fall_back_to_memory()
    _memory_store[profile_name] = payload


def load_credentials(profile_name: str) -> StoredCredentials | None:
    payload = None
    if not _keyring_backend_failed:
        try:
            payload = keyring.get_password(_SERVICE_NAME, profile_name)
        except _KEYRING_UNAVAILABLE_ERRORS:
            _fall_back_to_memory()
    if payload is None:
        payload = _memory_store.get(profile_name)
    if payload is None:
        return None
    return StoredCredentials(**json.loads(payload))


def delete_credentials(profile_name: str) -> None:
    _memory_store.pop(profile_name, None)
    if not _keyring_backend_failed:
        try:
            keyring.delete_password(_SERVICE_NAME, profile_name)
        except keyring.errors.PasswordDeleteError:
            pass
        except _KEYRING_UNAVAILABLE_ERRORS:
            _fall_back_to_memory()
