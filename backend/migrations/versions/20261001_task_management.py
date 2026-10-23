"""Deduplicate direct assignments and retain withdrawn task records."""

import sqlalchemy as sa
from alembic import op

revision = "20261001_task_management"
down_revision = "20260926_assigner_departments"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("withdrawal_reason", sa.Text(), nullable=False, server_default=""))
    op.create_table(
        "direct_assignment_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_direct_assignment_request"),
    )
    op.create_index("ix_direct_assignment_requests_task_id", "direct_assignment_requests", ["task_id"])


def downgrade():
    op.drop_table("direct_assignment_requests")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("withdrawal_reason")
        batch.drop_column("withdrawn_at")
