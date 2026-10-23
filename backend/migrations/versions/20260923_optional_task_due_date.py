"""Allow meeting tasks without a planned completion date."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_optional_task_due_date"
down_revision: Union[str, Sequence[str], None] = "20260922_native_todo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("due_date", existing_type=sa.Date(), nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM tasks WHERE due_date IS NULL LIMIT 1")).first():
        raise RuntimeError("有未设置截止日期的任务，无法恢复非空约束")
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("due_date", existing_type=sa.Date(), nullable=False)
