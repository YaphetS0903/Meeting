"""Allow special meeting series without a department."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_special_meeting_series"
down_revision: Union[str, Sequence[str], None] = "20260923_optional_task_due_date"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("meeting_series") as batch:
        batch.alter_column("department_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM meeting_series WHERE department_id IS NULL LIMIT 1")).first():
        raise RuntimeError("存在无所属部门的专项会议系列，无法恢复非空约束")
    with op.batch_alter_table("meeting_series") as batch:
        batch.alter_column("department_id", existing_type=sa.String(), nullable=False)
