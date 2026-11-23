"""Add independent department report and meeting task-sheet roles."""
import sqlalchemy as sa
from alembic import op

revision = "20261103_employee_roles"
down_revision = "20261030_report_grants"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "employee_role_grants",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(40), primary_key=True),
        sa.CheckConstraint("role IN ('department_reports', 'department_meetings')", name="ck_employee_role"),
    )


def downgrade():
    op.drop_table("employee_role_grants")
