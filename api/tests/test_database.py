import pandas as pd

from glow_api import database


def test_extract_schools_from_dataframe_matches_by_odk_school_id_not_name(
    db_session,
):
    """A school renamed by an admin must still be recognized as the same
    school on the next sync (matched by odk_school_id), not re-created as a
    duplicate keyed by the now-stale name."""
    df = pd.DataFrame({"school": ["Beahanberg High School", "Beahanberg High School"]})

    [school] = database.extract_schools_from_dataframe(db_session, df)
    assert school.name == "Beahanberg High School"
    assert school.odk_school_id == "Beahanberg High School"

    school.name = "Beahanberg High (renamed)"
    db_session.commit()

    [school_again] = database.extract_schools_from_dataframe(db_session, df)
    assert school_again.id == school.id
    assert school_again.name == "Beahanberg High (renamed)"
    assert len(database.list_schools(db_session)) == 1


def test_extract_schools_from_dataframe_backfills_legacy_name_matched_school(
    db_session,
):
    """A School row created before odk_school_id existed (name == the raw ODK
    value, odk_school_id null) must be recognized and backfilled, not
    collide with the name-uniqueness constraint on a fresh insert attempt."""
    legacy = database.create_school(db_session, name="Beahanberg High School")
    assert legacy.odk_school_id is None

    df = pd.DataFrame({"school": ["Beahanberg High School"]})
    [school] = database.extract_schools_from_dataframe(db_session, df)

    assert school.id == legacy.id
    assert school.odk_school_id == "Beahanberg High School"
    assert len(database.list_schools(db_session)) == 1


def test_create_metadata_engine_uses_sqlite_thread_check(monkeypatch):
    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(database, "create_engine", fake_create_engine)

    database.create_metadata_engine("sqlite:///./metadata.db")

    assert captured == {
        "url": "sqlite:///./metadata.db",
        "kwargs": {"connect_args": {"check_same_thread": False}},
    }


def test_create_metadata_engine_omits_sqlite_only_options_for_postgres(monkeypatch):
    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(database, "create_engine", fake_create_engine)

    database.create_metadata_engine("postgresql+psycopg://glow:secret@api-db:5432/glow")

    assert captured == {
        "url": "postgresql+psycopg://glow:secret@api-db:5432/glow",
        "kwargs": {},
    }
