"""Allow administrators to designate department task assigners."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_assigner_departments"
down_revision: Union[str, Sequence[str], None] = "20260926_direct_assignments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("manual_leader_department_ids", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("manual_leader_department_ids")
