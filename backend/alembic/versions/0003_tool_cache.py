"""Add the persistent L3 tool result cache."""

from alembic import op
import sqlalchemy as sa

revision = "0003_tool_cache"
down_revision = "0002_storage_consistency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 该表只保存可由真实工具重新生成的派生结果，不承担业务事实或 Graph 状态。
    op.create_table(
        "tool_cache_entries",
        sa.Column("cache_key", sa.String(128), primary_key=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_tool_cache_entries_tool_name",
        "tool_cache_entries",
        ["tool_name"],
    )
    op.create_index(
        "ix_tool_cache_entries_expires_at",
        "tool_cache_entries",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tool_cache_entries_expires_at",
        table_name="tool_cache_entries",
    )
    op.drop_index(
        "ix_tool_cache_entries_tool_name",
        table_name="tool_cache_entries",
    )
    op.drop_table("tool_cache_entries")
