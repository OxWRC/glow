"""API key generation/hashing helpers.

Keys are never stored raw - only their sha256 hash. The prefix is stored
alongside for display (so a user can tell keys apart in a list) without
ever re-exposing the secret.
"""

import hashlib
import secrets

KEY_PREFIX = "glow_"
DISPLAY_PREFIX_LEN = 12


def generate_api_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def display_prefix(raw_key: str) -> str:
    return raw_key[:DISPLAY_PREFIX_LEN]
