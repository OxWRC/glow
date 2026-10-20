"""oidc_sub rename and unique email

Revision ID: b7d2e41c9a10
Revises: a3f1c9d27b44
Create Date: 2026-10-20 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b7d2e41c9a10"
down_revision: Union[str, None] = "a3f1c9d27b44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE users SET email = lower(trim(email)) WHERE email IS NOT NULL")
    op.execute("UPDATE users SET email = NULL WHERE email = ''")
    op.drop_index("ix_users_cognito_sub", table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("cognito_sub", new_column_name="oidc_sub")
        batch_op.create_unique_constraint("uq_users_email", ["email"])
    op.create_index("ix_users_oidc_sub", "users", ["oidc_sub"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_oidc_sub", table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_email", type_="unique")
        batch_op.alter_column("oidc_sub", new_column_name="cognito_sub")
    op.create_index("ix_users_cognito_sub", "users", ["cognito_sub"], unique=True)
