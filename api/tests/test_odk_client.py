"""Tests for ODKClient's OData-based submission fetching."""

import pytest

from glow_api.odk_client import ODKClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ODKClient, "_try_login", lambda self: True)
    return ODKClient(
        base_url="http://odk.test",
        username="u",
        password="p",
        project_id=1,
    )


class FakeResponse:
    def __init__(self, payload=None, status_code=200, etag=None):
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = {"etag": etag} if etag else {}

    def json(self):
        return self._payload


def test_fetch_form_submissions_flattens_and_paginates(client, monkeypatch):
    pages = {
        "http://odk.test/v1/projects/1/forms/f1.svc/Submissions": FakeResponse(
            payload={
                "value": [
                    {
                        "__id": "a",
                        "meta": {"instanceID": "a"},
                        "q1": 3,
                        "__system": {
                            "submissionDate": "2024-01-01T00:00:00Z",
                            "updatedAt": None,
                            "formVersion": "2",
                        },
                    }
                ],
                "@odata.nextLink": "http://odk.test/next",
            },
            etag="etag-1",
        ),
        "http://odk.test/next": FakeResponse(
            payload={
                "value": [
                    {
                        "__id": "b",
                        "meta": {"instanceID": "b"},
                        "q1": 5,
                        "__system": {
                            "submissionDate": "2024-01-02T00:00:00Z",
                            "updatedAt": "2024-01-02T01:00:00Z",
                            "formVersion": "2",
                        },
                    }
                ],
            }
        ),
    }
    monkeypatch.setattr(client, "get", lambda url, **kw: pages[url])

    df, etag = client.fetch_form_submissions(form_id="f1")

    assert etag == "etag-1"
    assert len(df) == 2
    assert list(df["instanceId"]) == ["a", "b"]
    assert list(df["createdAt"]) == ["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"]
    assert list(df["__version"]) == ["2", "2"]
    assert list(df["__xmlFormId"]) == ["f1", "f1"]
    assert list(df["q1"]) == [3, 5]


def test_fetch_form_submissions_304_returns_none(client, monkeypatch):
    monkeypatch.setattr(client, "get", lambda url, **kw: FakeResponse(status_code=304))

    df, etag = client.fetch_form_submissions(form_id="f1", etag="unchanged")

    assert df is None
    assert etag == "unchanged"


@pytest.fixture
def unauthenticated_client(monkeypatch):
    monkeypatch.setattr(ODKClient, "_try_login", lambda self: False)
    return ODKClient(
        base_url="http://odk.test",
        username="u",
        password="wrong",
        project_id=1,
    )


def test_fetch_submissions_raises_when_login_fails(unauthenticated_client):
    # An empty result here would be indistinguishable from "no submissions",
    # so DataStore would replace good data (and its cache) with nothing.
    with pytest.raises(RuntimeError, match="login failed"):
        unauthenticated_client.fetch_submissions()


def test_get_form_metadata_raises_when_login_fails(unauthenticated_client):
    with pytest.raises(RuntimeError, match="login failed"):
        unauthenticated_client.get_form_metadata()


def test_datastore_keeps_data_and_cache_when_login_fails(
    unauthenticated_client, tmp_path
):
    import pandas as pd

    from glow_api.data import DataStore

    cache_path = tmp_path / "cache.parquet"
    ds = DataStore(
        odk_client=unauthenticated_client, refresh_hours=0, cache_path=cache_path
    )
    ds._df = pd.DataFrame({"uid": ["S001"]})

    ds.refresh()

    assert list(ds.to_frozen().df["uid"]) == ["S001"]
    assert not cache_path.exists()
