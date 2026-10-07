"""drop users is_wrc

Revision ID: 12d38729753d
Revises: 0cc26ad57204
Create Date: 2026-10-07 10:08:54.359498

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "12d38729753d"
down_revision: Union[str, None] = "0cc26ad57204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("is_wrc")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("is_wrc", sa.Boolean(), nullable=False, server_default=sa.false())
        )
