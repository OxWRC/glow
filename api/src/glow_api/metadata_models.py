"""
SQLAlchemy models for the metadata database (users and school metadata).
Separate from the read-only CSV data.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """DateTime that is always timezone-aware, in and out.

    SQLite (used in tests, and for local dev) has no native tz-aware storage -
    it silently drops tzinfo on read-back even from a DateTime(timezone=True)
    column. This type re-attaches UTC on the way out (and normalizes to UTC on
    the way in) so every datetime the app sees is aware, on any backend.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


# Association tables for many-to-many relationships
school_geographical_neighbors = Table(
    "school_geographical_neighbors",
    Base.metadata,
    Column("school_id", Integer, ForeignKey("schools.id"), primary_key=True),
    Column("neighbor_id", Integer, ForeignKey("schools.id"), primary_key=True),
)

school_statistical_neighbors = Table(
    "school_statistical_neighbors",
    Base.metadata,
    Column("school_id", Integer, ForeignKey("schools.id"), primary_key=True),
    Column("neighbor_id", Integer, ForeignKey("schools.id"), primary_key=True),
)

user_schools = Table(
    "user_schools",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("school_id", Integer, ForeignKey("schools.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False, unique=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    cognito_sub = Column(String, nullable=True, unique=True, index=True)
    is_wrc = Column(Boolean, nullable=False, default=False)
    email = Column(String, nullable=True)

    # Many-to-many with schools
    schools = relationship(
        "School",
        secondary=user_schools,
        back_populates="users",
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    prefix = Column(String, nullable=False)
    key_hash = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(
        UTCDateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    expires_at = Column(UTCDateTime, nullable=False)
    revoked_at = Column(UTCDateTime, nullable=True)
    last_used_at = Column(UTCDateTime, nullable=True)

    user = relationship("User")


class School(Base):
    __tablename__ = "schools"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True, index=True)
    size = Column(String, nullable=True)  # e.g., "Small", "Medium", "Large"
    category = Column(String, nullable=True)  # e.g., "Academy", "Comprehensive"
    # Raw ODK submission "school" field value this school is linked to. Join
    # key for matching submission data - kept separate from `name` so an
    # admin can rename a school for display without breaking the link, and
    # so a school with no ODK data yet (not onboarded) can exist with this
    # left null.
    odk_school_id = Column(String, nullable=True, unique=True, index=True)

    # Many-to-many with users
    users = relationship(
        "User",
        secondary=user_schools,
        back_populates="schools",
    )

    # Self-referential many-to-many for geographical neighbors
    geographical_neighbors = relationship(
        "School",
        secondary=school_geographical_neighbors,
        primaryjoin=id == school_geographical_neighbors.c.school_id,
        secondaryjoin=id == school_geographical_neighbors.c.neighbor_id,
        backref="geographical_neighbor_of",
    )

    # Self-referential many-to-many for statistical neighbors
    statistical_neighbors = relationship(
        "School",
        secondary=school_statistical_neighbors,
        primaryjoin=id == school_statistical_neighbors.c.school_id,
        secondaryjoin=id == school_statistical_neighbors.c.neighbor_id,
        backref="statistical_neighbor_of",
    )
