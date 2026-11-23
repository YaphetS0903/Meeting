"""Allow system administrators to delegate department report access."""

import sqlalchemy as sa
from alembic import op

revision = "20261030_report_grants"
down_revision = "20261028_task_carry_replacement"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "report_department_grants",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("department_id", sa.String(36), sa.ForeignKey("departments.id", ondelete="CASCADE"),
                  primary_key=True),
    )


def downgrade():
    op.drop_table("report_department_grants")
