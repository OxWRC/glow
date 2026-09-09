"""add_school_odk_school_id

Revision ID: 5c59c67781ff
Revises: 6f7b8cbdce8d
Create Date: 2026-09-09 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5c59c67781ff"
down_revision: Union[str, None] = "6f7b8cbdce8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("schools", sa.Column("odk_school_id", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_schools_odk_school_id"), "schools", ["odk_school_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_schools_odk_school_id"), table_name="schools")
    op.drop_column("schools", "odk_school_id")
