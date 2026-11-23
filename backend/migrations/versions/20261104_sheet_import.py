"""Keep task-sheet recognition sources for editable online meeting drafts."""
import sqlalchemy as sa
from alembic import op

revision = "20261104_sheet_import"
down_revision = "20261104_dispatch_role"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "meeting_sheet_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("series_id", sa.String(36), sa.ForeignKey("meeting_series.id", ondelete="SET NULL")),
        sa.Column("creator_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("attachment_id", sa.String(36), sa.ForeignKey("attachments.id"), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("selected_index", sa.Integer()),
        sa.Column("meeting_id", sa.String(36), sa.ForeignKey("meetings.id", ondelete="SET NULL"), unique=True),
        sa.Column("fingerprint", sa.String(64), unique=True),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM meeting_sheet_imports")):
        raise RuntimeError("存在任务单导入来源，不能删除追溯记录")
    op.drop_table("meeting_sheet_imports")
