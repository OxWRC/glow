"""
SQLAlchemy models for the metadata database (users and school metadata).
Separate from the read-only CSV data.
"""

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import DeclarativeBase, relationship


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
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    is_admin = Column(Boolean, nullable=False, default=False)

    # Many-to-many with schools
    schools = relationship(
        "School",
        secondary=user_schools,
        back_populates="users",
    )


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
