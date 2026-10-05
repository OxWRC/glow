"""
Database configuration and session management for metadata database.
"""

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from glow_api.metadata_models import ApiKey, School, User
from glow_api.settings import settings


def create_metadata_engine(database_url: str):
    engine_kwargs = {}

    if make_url(database_url).get_backend_name() == "sqlite":
        engine_kwargs["connect_args"] = {"check_same_thread": False}

    return create_engine(database_url, **engine_kwargs)


# Create engine for metadata database
engine = create_metadata_engine(settings.METADATA_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _alembic_ini_path() -> Path:
    """Locate alembic.ini in the project (supports both dev and installed modes)."""
    import os

    # Allow override via environment variable (for production deployments)
    env_path = os.getenv("GLOW_ALEMBIC_INI")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path
        raise FileNotFoundError(
            f"GLOW_ALEMBIC_INI points to non-existent file: {env_path}"
        )

    # Search upward from current file (for development/editable installs)
    here = Path(__file__).parent
    level = 0
    while level < 5:
        ini = here / "alembic.ini"
        if ini.exists():
            return ini
        here = here.parent
        level += 1
    raise FileNotFoundError(
        "alembic.ini not found within 5 parent directories of database.py. "
        "Ensure it is present in the api/ directory or set GLOW_ALEMBIC_INI."
    )


def run_migrations() -> None:
    """Apply all pending Alembic migrations (used at application startup)."""
    import logging

    logger = logging.getLogger(__name__)

    logger.info("run_migrations: Finding alembic.ini...")
    ini_path = _alembic_ini_path()
    logger.info(f"run_migrations: Found at {ini_path}")
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(ini_path.parent / "alembic"))
    cfg.set_main_option("prepend_sys_path", str(ini_path.parent / "src"))
    # Override the URL from settings so env vars are respected
    cfg.set_main_option("sqlalchemy.url", settings.METADATA_DATABASE_URL)
    logger.info("run_migrations: Running alembic upgrade...")
    logger.info(
        f"run_migrations: Database URL: {settings.METADATA_DATABASE_URL.split('@')[1] if '@' in settings.METADATA_DATABASE_URL else 'unknown'}"
    )

    try:
        command.upgrade(cfg, "head")
        logger.info("run_migrations: Upgrade complete")
    except Exception as e:
        logger.error(f"run_migrations: Failed with error: {e}")
        raise


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that provides a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# User CRUD operations
def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def get_user_by_sub(db: Session, cognito_sub: str) -> User | None:
    return db.query(User).filter(User.cognito_sub == cognito_sub).first()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def upsert_user_by_sub(
    db: Session,
    cognito_sub: str,
    username: str | None = None,
    is_admin: bool = False,
    is_wrc: bool = False,
    school_ids: list[int] | None = None,
) -> User:
    """Find or create the local User row for a given Cognito `sub`, syncing
    is_admin/is_wrc/school_ids to the given values on every call.

    Used by the demo-mode login (Task 4) and by real-Cognito bootstrap
    (Task 5) to link a local row to an identity that only exists as a token
    claim - `get_user_by_sub` then finds a real row for the rest of the auth
    stack. `username` defaults to `cognito_sub` when creating a new row (only
    used then; an existing row's username is left alone here - that's
    `sync_user_claims`'s job).

    If no row matches `cognito_sub` but one already exists with the given
    `username` (necessarily with `cognito_sub` still NULL - e.g. a row made
    by the pre-Cognito `create_user` path), that row is linked by attaching
    `cognito_sub` to it rather than attempting to insert a second row and
    hitting the `username` unique constraint.
    """
    if is_wrc:
        # WRC users never have direct school access, regardless of what was
        # passed for schools (mirrors create_user/update_user).
        school_ids = []
    user = get_user_by_sub(db, cognito_sub)
    if user is None and username is not None:
        user = get_user_by_username(db, username)
        if user is not None:
            user.cognito_sub = cognito_sub
    if user is None:
        user = User(
            username=username or cognito_sub,
            cognito_sub=cognito_sub,
            is_admin=is_admin,
            is_wrc=is_wrc,
        )
        db.add(user)
    else:
        user.is_admin = is_admin
        user.is_wrc = is_wrc
    if school_ids is not None:
        schools = db.query(School).filter(School.id.in_(school_ids)).all()
        user.schools = schools
    db.commit()
    db.refresh(user)
    return user


def create_user(
    db: Session,
    username: str,
    is_active: bool = True,
    is_admin: bool = False,
    is_wrc: bool = False,
    school_ids: list[int] | None = None,
) -> User:
    if is_wrc:
        # WRC users never have direct school access, regardless of what
        # was passed for schools.
        school_ids = []
    user = User(
        username=username,
        is_active=is_active,
        is_admin=is_admin,
        is_wrc=is_wrc,
    )
    if school_ids:
        schools = db.query(School).filter(School.id.in_(school_ids)).all()
        user.schools = schools
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(
    db: Session,
    user: User,
    is_active: bool | None = None,
    is_admin: bool | None = None,
    is_wrc: bool | None = None,
    school_ids: list[int] | None = None,
) -> User:
    if is_active is not None:
        user.is_active = is_active
    if is_admin is not None:
        user.is_admin = is_admin
    if is_wrc is not None:
        user.is_wrc = is_wrc
    if user.is_wrc:
        # WRC users never have direct school access, regardless of what
        # was passed for schools (including nothing at all - flipping
        # is_wrc=True must clear any schools the user already had).
        school_ids = []
    if school_ids is not None:
        schools = db.query(School).filter(School.id.in_(school_ids)).all()
        user.schools = schools
    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, user: User) -> None:
    db.delete(user)
    db.commit()


def list_users(db: Session) -> list[User]:
    return db.query(User).all()


# School CRUD operations
def get_school_by_name(db: Session, name: str) -> School | None:
    return db.query(School).filter(School.name == name).first()


def get_school_by_odk_school_id(db: Session, odk_school_id: str) -> School | None:
    return db.query(School).filter(School.odk_school_id == odk_school_id).first()


def get_school_by_id(db: Session, school_id: int) -> School | None:
    return db.query(School).filter(School.id == school_id).first()


def create_school(
    db: Session,
    name: str,
    size: str | None = None,
    category: str | None = None,
    odk_school_id: str | None = None,
) -> School:
    school = School(
        name=name, size=size, category=category, odk_school_id=odk_school_id
    )
    db.add(school)
    db.commit()
    db.refresh(school)
    return school


def update_school(
    db: Session,
    school: School,
    name: str | None = None,
    size: str | None = None,
    category: str | None = None,
    odk_school_id: str | None = None,
) -> School:
    if name is not None:
        school.name = name
    if size is not None:
        school.size = size
    if category is not None:
        school.category = category
    if odk_school_id is not None:
        school.odk_school_id = odk_school_id
    db.commit()
    db.refresh(school)
    return school


def delete_school(db: Session, school: School) -> None:
    db.delete(school)
    db.commit()


def list_schools(db: Session) -> list[School]:
    return db.query(School).all()


def set_geographical_neighbors(
    db: Session, school: School, neighbor_ids: list[int]
) -> School:
    """Set geographical neighbors for a school (reciprocal relationship)."""
    neighbors = db.query(School).filter(School.id.in_(neighbor_ids)).all()
    school.geographical_neighbors = neighbors

    # Make reciprocal
    for neighbor in neighbors:
        if school not in neighbor.geographical_neighbors:
            neighbor.geographical_neighbors.append(school)

    db.commit()
    db.refresh(school)
    return school


def set_statistical_neighbors(
    db: Session, school: School, neighbor_ids: list[int]
) -> School:
    """Set statistical neighbors for a school (reciprocal relationship)."""
    neighbors = db.query(School).filter(School.id.in_(neighbor_ids)).all()
    school.statistical_neighbors = neighbors

    # Make reciprocal
    for neighbor in neighbors:
        if school not in neighbor.statistical_neighbors:
            neighbor.statistical_neighbors.append(school)

    db.commit()
    db.refresh(school)
    return school


def extract_schools_from_dataframe(db: Session, df) -> list[School]:
    """Extract unique ODK school values from a DataFrame and create School records.

    Matches/links by odk_school_id (the raw ODK "school" field value), not by
    display name, so a school already renamed by an admin doesn't get
    re-created as a duplicate. Falls back to matching by name for schools
    created before odk_school_id existed (name == the raw ODK value back
    then) and backfills their odk_school_id, rather than colliding with
    them on the name uniqueness constraint.

    Returns list of created/existing schools.
    """
    if "school" not in df.columns:
        raise ValueError("DataFrame does not contain 'school' column")

    unique_schools = df["school"].dropna().unique()
    created_schools = []

    for odk_school_id in sorted(unique_schools):
        existing = get_school_by_odk_school_id(db, odk_school_id)
        if existing is None:
            legacy = get_school_by_name(db, odk_school_id)
            if legacy is not None and legacy.odk_school_id is None:
                created_schools.append(
                    update_school(db, legacy, odk_school_id=odk_school_id)
                )
                continue
            school = create_school(db, name=odk_school_id, odk_school_id=odk_school_id)
            created_schools.append(school)
        else:
            created_schools.append(existing)

    return created_schools


# API key CRUD operations
def create_api_key(
    db: Session,
    user_id: int,
    name: str,
    key_hash: str,
    prefix: str,
    expires_at: datetime,
) -> ApiKey:
    api_key = ApiKey(
        user_id=user_id,
        name=name,
        key_hash=key_hash,
        prefix=prefix,
        expires_at=expires_at,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return api_key


def get_api_key_by_hash(db: Session, key_hash: str) -> ApiKey | None:
    return db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()


def get_api_key_by_id(db: Session, key_id: int) -> ApiKey | None:
    return db.query(ApiKey).filter(ApiKey.id == key_id).first()


def list_api_keys_for_user(db: Session, user_id: int) -> list[ApiKey]:
    return db.query(ApiKey).filter(ApiKey.user_id == user_id).all()


def revoke_api_key(db: Session, api_key: ApiKey) -> ApiKey:
    api_key.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(api_key)
    return api_key


def touch_api_key_last_used(db: Session, api_key: ApiKey) -> None:
    api_key.last_used_at = datetime.now(timezone.utc)
    db.commit()


def grant_admins_all_schools(db: Session) -> int:
    """Grant all admin users access to all schools.

    Returns the number of admin users updated.
    """
    admins = db.query(User).filter(User.is_admin).all()
    all_schools = list_schools(db)

    updated_count = 0
    for admin in admins:
        # Set all schools for this admin
        admin.schools = all_schools
        updated_count += 1

    db.commit()
    return updated_count
