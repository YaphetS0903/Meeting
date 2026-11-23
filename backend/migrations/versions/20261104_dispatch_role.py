"""Add the independent company-wide direct-dispatch role."""
import sqlalchemy as sa
from alembic import op

revision = "20261104_dispatch_role"
down_revision = "20261103_employee_roles"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("employee_role_grants") as batch:
        batch.drop_constraint("ck_employee_role", type_="check")
        batch.create_check_constraint(
            "ck_employee_role",
            "role IN ('department_reports', 'department_meetings', 'company_dispatch')",
        )


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT count(*) FROM employee_role_grants WHERE role = 'company_dispatch'"
    )):
        raise RuntimeError("请先撤销派发任务单管理员授权，再回退数据库结构")
    with op.batch_alter_table("employee_role_grants") as batch:
        batch.drop_constraint("ck_employee_role", type_="check")
        batch.create_check_constraint("ck_employee_role", "role IN ('department_reports', 'department_meetings')")
