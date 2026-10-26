"""add audit events

Revision ID: d9a3b6c1e5f7
Revises: c4e8a1f0d2b3
Create Date: 2026-10-26 13:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d9a3b6c1e5f7"
down_revision: Union[str, None] = "c4e8a1f0d2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_ts", "audit_events", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_ts", table_name="audit_events")
    op.drop_table("audit_events")
