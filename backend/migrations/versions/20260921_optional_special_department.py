"""Allow special meetings without a department and meetings without a recorder."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260921_optional_fields"
down_revision: Union[str, Sequence[str], None] = "20260921_recurrence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("meetings") as batch:
        batch.alter_column("department_id", existing_type=sa.String(length=36), nullable=True)
        batch.alter_column("recorder_id", existing_type=sa.String(length=36), nullable=True)
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("department_id", existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("department_id", existing_type=sa.String(length=36), nullable=False)
    with op.batch_alter_table("meetings") as batch:
        batch.alter_column("recorder_id", existing_type=sa.String(length=36), nullable=False)
        batch.alter_column("department_id", existing_type=sa.String(length=36), nullable=False)
