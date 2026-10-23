"""Invalidate old sessions when a local password changes."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260924_password_change"
down_revision: Union[str, Sequence[str], None] = "20260924_meeting_edit_approval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("auth_version")
