"""api keys admin issued

Revision ID: a3f1c9d27b44
Revises: 12d38729753d
Create Date: 2026-10-07 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a3f1c9d27b44"
down_revision: Union[str, None] = "12d38729753d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing keys belonged to WRC users, a role that no longer exists.
    op.execute("DELETE FROM api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_index("ix_api_keys_user_id")
        batch_op.drop_column("user_id")
        batch_op.add_column(
            sa.Column("use_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("created_by_user_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_api_keys_created_by_user_id_users",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    op.execute("DELETE FROM api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_constraint(
            "fk_api_keys_created_by_user_id_users", type_="foreignkey"
        )
        batch_op.drop_column("created_by_user_id")
        batch_op.drop_column("use_count")
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=False))
        batch_op.create_foreign_key(
            "fk_api_keys_user_id_users", "users", ["user_id"], ["id"]
        )
        batch_op.create_index("ix_api_keys_user_id", ["user_id"])
