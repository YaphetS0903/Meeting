"""Record confirmed continuity between legacy task identifiers."""

import sqlalchemy as sa
from alembic import op

revision = "20261028_task_carry_replacement"
down_revision = "20261013_app_native_todo"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_carry_replacements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("next_task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meeting_id", sa.String(36), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meeting_version", sa.Integer(), nullable=False),
        sa.Column("previous_task_version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint("previous_task_id <> next_task_id", name="carry_distinct_tasks"),
    )
    op.create_index("ix_task_carry_replacements_next_task_id", "task_carry_replacements", ["next_task_id"])
    op.create_index("ix_task_carry_replacements_meeting_id", "task_carry_replacements", ["meeting_id"])


def downgrade():
    op.drop_table("task_carry_replacements")
