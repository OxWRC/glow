"""Pytest fixtures for glow-api tests."""

import io
import os
import threading

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from glow_api.auth import get_current_user, get_password_hash
from glow_api.data import DataStore
from glow_api.database import create_user, create_school, get_db
from glow_api.metadata_models import Base
from glow_api.main import app
from tests.mock_odk import MockODKClient

# Set testing flag to skip datastore initialization in lifespan
os.environ["GLOW_TESTING"] = "1"

# ---------------------------------------------------------------------------
# Sample DataFrame used across tests
# ---------------------------------------------------------------------------

SAMPLE_CSV = """uid,wave,school,yearGroup,class,d_sex,d_ethnicity,d_age,d_city,d_country,bw_wbeing_1,bw_wbeing_2,bw_wbeing_3
S001,1,Focus School Academy,7,A,M,White,12.5,Oxford,UK,3,4,2
S002,1,Focus School Academy,7,A,F,White,12.3,Oxford,UK,4,3,4
S003,1,Focus School Academy,7,B,M,Asian,12.8,Oxford,UK,2,3,3
S004,1,Focus School Academy,7,B,F,Asian,12.1,Oxford,UK,4,2,3
S005,1,Focus School Academy,7,C,F,Black,12.4,Oxford,UK,3,4,3
S006,1,Neighbouring School,8,A,M,White,13.0,London,UK,3,3,3
S007,1,Neighbouring School,8,A,F,Black,13.2,London,UK,4,4,4
S008,1,Neighbouring School,8,B,M,White,13.5,London,UK,2,2,2
S009,1,Neighbouring School,8,B,F,Asian,13.1,London,UK,3,3,3
S010,1,Neighbouring School,8,C,F,White,13.4,London,UK,3,2,3
S001,2,Focus School Academy,7,A,M,White,13.5,Oxford,UK,4,3,3
S002,2,Focus School Academy,7,A,F,White,13.3,Oxford,UK,3,4,4
S003,2,Focus School Academy,7,B,M,Asian,13.8,Oxford,UK,3,3,4
S004,2,Focus School Academy,7,B,F,Asian,13.1,Oxford,UK,4,3,3
S005,2,Focus School Academy,7,C,F,Black,13.4,Oxford,UK,3,4,3
"""

TINY_CSV = """uid,wave,school,d_sex,bw_wbeing_1
S001,1,Focus School Academy,M,3
S002,1,Focus School Academy,F,4
"""


def _make_df(csv_text: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(csv_text))


# ---------------------------------------------------------------------------
# In-memory SQLite for tests
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite://"  # in-memory


@pytest.fixture(scope="function")
def db_engine():
    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def sample_schools(db_session):
    """Create sample schools in the test DB."""
    alpha = create_school(
        db_session,
        name="Focus School Academy",
        size="medium",
        category="comprehensive",
        odk_school_id="Focus School Academy",
    )
    beta = create_school(
        db_session,
        name="Neighbouring School",
        size="large",
        category="academy",
        odk_school_id="Neighbouring School",
    )
    return {"Focus School Academy": alpha, "Neighbouring School": beta}


@pytest.fixture(scope="function")
def sample_user(db_session, sample_schools):
    """Create a sample user in the test DB with access to Focus School Academy school."""
    user = create_user(
        db_session,
        username="testuser",
        hashed_password=get_password_hash("testpass"),
        school_ids=[sample_schools["Focus School Academy"].id],
    )
    return user


@pytest.fixture(scope="function")
def admin_user(db_session, sample_schools):
    """Create an admin user in the test DB with access to all schools."""
    user = create_user(
        db_session,
        username="adminuser",
        hashed_password=get_password_hash("adminpass"),
        school_ids=[
            sample_schools["Focus School Academy"].id,
            sample_schools["Neighbouring School"].id,
        ],
        is_admin=True,
    )
    return user


@pytest.fixture(scope="function")
def sample_df():
    df = _make_df(SAMPLE_CSV)
    # Compute derived scores like the real DataStore does
    mock_client = MockODKClient()
    ds = DataStore(odk_client=mock_client, refresh_hours=0)
    df = ds._process_loaded_data(df)
    return df


@pytest.fixture(scope="function")
def tiny_df():
    return _make_df(TINY_CSV)


@pytest.fixture(scope="function")
def empty_df():
    """Mimics datastore state before the first successful ODK sync.

    Normalized (has period_id) but has no "school" column, since no
    submissions have been loaded yet.
    """
    mock_client = MockODKClient()
    ds = DataStore(odk_client=mock_client, refresh_hours=0)
    return ds._process_loaded_data(pd.DataFrame())


def _make_mock_datastore(df: pd.DataFrame, metadata: dict = None) -> DataStore:
    """Helper to create a mock DataStore with pre-loaded data.

    Args:
        df: DataFrame to inject into the DataStore
        metadata: Optional metadata dict

    Returns:
        DataStore instance with data already loaded
    """
    if metadata is None:
        metadata = {}

    mock_client = MockODKClient(submissions_df=df, metadata=metadata)
    ds = DataStore(odk_client=mock_client, refresh_hours=0)
    ds._df = ds._process_loaded_data(df)
    ds._metadata = metadata
    ds._lock = threading.Lock()
    ds.startup = lambda: None
    ds.shutdown = lambda: None
    return ds


# ---------------------------------------------------------------------------
# Test client with overridden dependencies
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def client(db_session, sample_user, sample_schools, sample_df):
    """TestClient with DB and datastore overridden."""
    from glow_api.models import UserRead

    # Override DB dependency
    def override_get_db():
        yield db_session

    # Override data store with sample data
    fake_store = _make_mock_datastore(sample_df)

    def override_get_datastore():
        return fake_store

    # Override current user dependency to return a known user
    def override_get_current_user():
        return UserRead(
            id=sample_user.id,
            username=sample_user.username,
            school_ids=[s.id for s in sample_user.schools],
            school_names=[s.name for s in sample_user.schools],
            is_active=True,
            is_admin=False,
        )

    from glow_api.data import get_datastore

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_datastore] = override_get_datastore

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def auth_client(db_session, sample_user, sample_schools, sample_df):
    """TestClient WITHOUT auth override — uses real JWT flow."""
    from glow_api.data import get_datastore

    fake_store = _make_mock_datastore(sample_df)

    def override_get_datastore():
        return fake_store

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_datastore] = override_get_datastore

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def auth_client_empty_data(db_session, sample_user, sample_schools, empty_df):
    """TestClient with real JWT auth flow, backed by a datastore with no data loaded yet."""
    from glow_api.data import get_datastore

    fake_store = _make_mock_datastore(empty_df)

    def override_get_datastore():
        return fake_store

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_datastore] = override_get_datastore

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def admin_client(db_session, admin_user, sample_schools, sample_df):
    """TestClient with an authenticated admin user."""
    from glow_api.models import UserRead
    import glow_api.data as data_module

    def override_get_db():
        yield db_session

    fake_store = _make_mock_datastore(sample_df)

    def override_get_current_user():
        return UserRead(
            id=admin_user.id,
            username=admin_user.username,
            school_ids=[s.id for s in admin_user.schools],
            school_names=[s.name for s in admin_user.schools],
            is_active=True,
            is_admin=True,
        )

    # Store original datastore
    original_datastore = data_module.datastore
    # Override module-level datastore
    data_module.datastore = fake_store

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
    # Restore original datastore
    data_module.datastore = original_datastore


@pytest.fixture(scope="function")
def login_as_user(auth_client, db_session):
    """Helper to login as a specific user and return JWT token."""

    def _login(username: str) -> str:
        # Get user from DB to check password
        from glow_api.database import get_user_by_username

        user = get_user_by_username(db_session, username)
        if not user:
            raise ValueError(f"User {username} not found")

        # Login with known password (tests create users with hashed_password)
        # For test users, we need to use the raw password "test_password"
        response = auth_client.post(
            "/token",
            data={"username": username, "password": "test_password"},
        )
        if response.status_code != 200:
            raise ValueError(f"Login failed for {username}: {response.json()}")
        return response.json()["access_token"]

    return _login


@pytest.fixture(scope="function")
def admin_token(auth_client, admin_user):
    """Get JWT token for admin user."""
    response = auth_client.post(
        "/token",
        data={"username": "adminuser", "password": "adminpass"},
    )
    return response.json()["access_token"]
