"""Store direct task origin and directory role/contact fields."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_direct_assignments"
down_revision: Union[str, Sequence[str], None] = "20260924_password_change"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("mobile", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("position", sa.String(length=120), nullable=False, server_default=""))
        batch.add_column(sa.Column("leader_department_ids", sa.JSON(), nullable=False, server_default="[]"))
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("source_meeting_id", existing_type=sa.String(length=36), nullable=True)
        batch.add_column(sa.Column("issuer_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key("fk_tasks_issuer_id_users", "users", ["issuer_id"], ["id"])
        batch.create_index("ix_tasks_issuer_id", ["issuer_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM tasks WHERE source_meeting_id IS NULL LIMIT 1")).first():
        raise RuntimeError("已存在直接派发的任务，无法恢复会议来源非空约束")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_issuer_id")
        batch.drop_constraint("fk_tasks_issuer_id_users", type_="foreignkey")
        batch.drop_column("issuer_id")
        batch.alter_column("source_meeting_id", existing_type=sa.String(length=36), nullable=False)
    with op.batch_alter_table("users") as batch:
        batch.drop_column("leader_department_ids")
        batch.drop_column("position")
        batch.drop_column("mobile")
