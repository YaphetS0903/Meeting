"""Preserve operation context and index the existing append-only audit history."""
import sqlalchemy as sa
from alembic import op

revision = "20261105_operation_logs"
down_revision = "20261104_sheet_import"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("audit_logs", sa.Column("context", sa.JSON(), nullable=False, server_default="{}"))
    op.create_index("ix_audit_log_created_id", "audit_logs", ["created_at", "id"])
    op.create_index("ix_audit_log_actor_created", "audit_logs", ["actor_id", "created_at"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM audit_logs WHERE CAST(context AS TEXT) <> '{}'")):
        raise RuntimeError("操作日志含追溯信息，不能删除日志上下文")
    op.drop_index("ix_audit_log_actor_created", table_name="audit_logs")
    op.drop_index("ix_audit_log_created_id", table_name="audit_logs")
    op.drop_column("audit_logs", "context")
