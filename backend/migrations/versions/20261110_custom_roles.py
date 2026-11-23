"""Custom business roles; preserve only explicit old grants."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "20261110_custom_roles"
down_revision = "20261105_operation_logs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "business_roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
    )
    op.create_table(
        "business_role_assignments",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "role_id", sa.String(36), sa.ForeignKey("business_roles.id", ondelete="CASCADE"), primary_key=True
        ),
    )
    op.create_index("ix_business_role_members", "business_role_assignments", ["role_id", "user_id"])
    from app.services.role_migration import migrate_explicit_grants

    # The surrounding Alembic transaction owns commit/rollback.
    with Session(bind=op.get_bind()) as db:
        migrate_explicit_grants(db)


def downgrade():
    raise RuntimeError("自定义角色包含授权配置，禁止自动降级；请使用部署前数据库备份恢复")
