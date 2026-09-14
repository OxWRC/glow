"""add_cognito_fields_drop_hashed_password

Revision ID: 452a473a98e1
Revises: 5c59c67781ff
Create Date: 2026-09-14 10:22:06.965121

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "452a473a98e1"
down_revision: Union[str, None] = "5c59c67781ff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("cognito_sub", sa.String(), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_wrc", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("email", sa.String(), nullable=True))
    op.create_index(op.f("ix_users_cognito_sub"), "users", ["cognito_sub"], unique=True)
    op.drop_column("users", "hashed_password")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("hashed_password", sa.VARCHAR(), nullable=False, server_default=""),
    )
    op.drop_index(op.f("ix_users_cognito_sub"), table_name="users")
    op.drop_column("users", "email")
    op.drop_column("users", "is_wrc")
    op.drop_column("users", "cognito_sub")
