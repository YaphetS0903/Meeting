"""Persist native WeCom todo user and task mappings."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260922_native_todo"
down_revision: Union[str, Sequence[str], None] = "20260921_optional_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("native_todo_userid", sa.String(length=120), nullable=True))
        batch.create_unique_constraint("uq_users_native_todo_userid", ["native_todo_userid"])
    op.create_table(
        "native_todo_links",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("todo_id", sa.String(length=200), nullable=False),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("follower_ids", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
        sa.UniqueConstraint("todo_id"),
    )


def downgrade() -> None:
    op.drop_table("native_todo_links")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_native_todo_userid", type_="unique")
        batch.drop_column("native_todo_userid")
