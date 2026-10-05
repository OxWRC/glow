import pytest

import glow_deploy.gui.secret_store as secret_store


class _FakeKeyringBackend:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    monkeypatch.setattr(secret_store, "_keyring_backend_failed", False)
    monkeypatch.setattr(secret_store, "_memory_store", {})


def _sample_credentials(**overrides):
    defaults = dict(
        access_key="AKIAEXAMPLE",
        secret_key="secret",
        session_token="token",
        expiration="2026-01-01T00:00:00Z",
        region="eu-west-2",
    )
    defaults.update(overrides)
    return secret_store.StoredCredentials(**defaults)


def test_save_and_load_round_trip_via_keyring(monkeypatch):
    backend = _FakeKeyringBackend()
    monkeypatch.setattr(secret_store.keyring, "set_password", backend.set_password)
    monkeypatch.setattr(secret_store.keyring, "get_password", backend.get_password)

    secret_store.save_credentials("default", _sample_credentials())
    loaded = secret_store.load_credentials("default")

    assert loaded == _sample_credentials()
    assert secret_store.backend_name() == "keyring"


def test_load_returns_none_when_nothing_stored(monkeypatch):
    monkeypatch.setattr(
        secret_store.keyring, "get_password", lambda service, username: None
    )

    assert secret_store.load_credentials("missing-profile") is None


def test_falls_back_to_memory_when_keyring_unavailable_on_save(monkeypatch):
    def _raise(*args, **kwargs):
        raise secret_store.keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr(secret_store.keyring, "set_password", _raise)

    with pytest.warns(RuntimeWarning, match="in memory"):
        secret_store.save_credentials("default", _sample_credentials())

    assert secret_store.backend_name() == "memory"
    assert secret_store.load_credentials("default") == _sample_credentials()


def test_falls_back_to_memory_when_keyring_unavailable_on_load(monkeypatch):
    def _raise(*args, **kwargs):
        raise secret_store.keyring.errors.KeyringLocked("locked")

    monkeypatch.setattr(secret_store.keyring, "get_password", _raise)

    with pytest.warns(RuntimeWarning, match="in memory"):
        result = secret_store.load_credentials("default")

    assert result is None
    assert secret_store.backend_name() == "memory"


def test_delete_credentials_removes_from_both_stores(monkeypatch):
    backend = _FakeKeyringBackend()
    monkeypatch.setattr(secret_store.keyring, "set_password", backend.set_password)
    monkeypatch.setattr(secret_store.keyring, "get_password", backend.get_password)
    monkeypatch.setattr(
        secret_store.keyring, "delete_password", backend.delete_password
    )

    secret_store.save_credentials("default", _sample_credentials())
    secret_store.delete_credentials("default")

    assert secret_store.load_credentials("default") is None


def test_delete_credentials_tolerates_nothing_stored(monkeypatch):
    def _raise(service, username):
        raise secret_store.keyring.errors.PasswordDeleteError("not found")

    monkeypatch.setattr(secret_store.keyring, "delete_password", _raise)

    secret_store.delete_credentials("never-saved")  # must not raise
