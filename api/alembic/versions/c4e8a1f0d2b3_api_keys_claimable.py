"""api keys claimable

Revision ID: c4e8a1f0d2b3
Revises: b7d2e41c9a10
Create Date: 2026-10-26 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4e8a1f0d2b3"
down_revision: Union[str, None] = "b7d2e41c9a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(sa.Column("email", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("notes", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.alter_column("name", existing_type=sa.String(), nullable=True)
        batch_op.alter_column("prefix", existing_type=sa.String(), nullable=True)
        batch_op.alter_column("key_hash", existing_type=sa.String(), nullable=True)
        batch_op.alter_column(
            "expires_at", existing_type=sa.DateTime(timezone=True), nullable=True
        )
    # Existing keys were revealed at creation, so they count as claimed then.
    op.execute("UPDATE api_keys SET claimed_at = created_at")


def downgrade() -> None:
    # Unclaimed keys have no hash and can't satisfy the old NOT NULLs.
    op.execute("DELETE FROM api_keys WHERE key_hash IS NULL")
    op.execute("UPDATE api_keys SET name = '' WHERE name IS NULL")
    op.execute(
        "UPDATE api_keys SET expires_at = '9999-12-31 00:00:00+00' "
        "WHERE expires_at IS NULL"
    )
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.alter_column(
            "expires_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.alter_column("key_hash", existing_type=sa.String(), nullable=False)
        batch_op.alter_column("prefix", existing_type=sa.String(), nullable=False)
        batch_op.alter_column("name", existing_type=sa.String(), nullable=False)
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("notes")
        batch_op.drop_column("email")
