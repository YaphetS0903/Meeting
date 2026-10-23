"""Separate application native todos from legacy robot todos."""

import sqlalchemy as sa
from alembic import op

revision = "20261013_app_native_todo"
down_revision = "20261001_task_management"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("native_todo_links") as batch:
        batch.add_column(sa.Column("provider", sa.String(20), nullable=False, server_default="cli"))
        batch.add_column(sa.Column("snapshot", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "native_todo_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
    )


def downgrade():
    op.drop_table("native_todo_attempts")
    with op.batch_alter_table("native_todo_links") as batch:
        batch.drop_column("snapshot")
        batch.drop_column("provider")
