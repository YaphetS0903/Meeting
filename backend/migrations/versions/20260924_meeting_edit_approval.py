"""Grant meeting editing through host approval."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260924_meeting_edit_approval"
down_revision: Union[str, Sequence[str], None] = "20260923_special_meeting_series"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("meetings") as batch:
        batch.add_column(sa.Column("permission_generation", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "meeting_edit_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), sa.ForeignKey("meetings.id"), nullable=False),
        sa.Column("applicant_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("host_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("permission_generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("reviewer_id", sa.String(length=36), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("meeting_id", "applicant_id", "permission_generation"),
    )
    op.create_index("ix_meeting_edit_requests_meeting_id", "meeting_edit_requests", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_meeting_edit_requests_meeting_id", table_name="meeting_edit_requests")
    op.drop_table("meeting_edit_requests")
    with op.batch_alter_table("meetings") as batch:
        batch.drop_column("permission_generation")
