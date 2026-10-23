"""Recurring calendar instances and unique historical activation identity."""

import sqlalchemy as sa
from alembic import op

revision = "20260921_recurrence"
down_revision = "9d105972d4d0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("recurrence_period", sa.Date(), nullable=True))
        batch.add_column(sa.Column("historical_work_id", sa.String(100), nullable=True))
        batch.create_unique_constraint("uq_task_recurrence_period", ["recurrence_key", "recurrence_period"])
        batch.create_unique_constraint("uq_task_historical_work", ["historical_work_id"])
    op.create_table(
        "recurring_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("creator_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("next_due_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("template", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("frequency IN ('weekly','monthly')", name="recurrence_frequency"),
    )


def downgrade():
    op.drop_table("recurring_rules")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("uq_task_historical_work", type_="unique")
        batch.drop_constraint("uq_task_recurrence_period", type_="unique")
        batch.drop_column("historical_work_id")
        batch.drop_column("recurrence_period")
