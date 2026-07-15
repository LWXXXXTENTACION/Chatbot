"""Add deterministic message ordering and an atomic sequence counter."""

from alembic import op
import sqlalchemy as sa

revision = "0002_storage_consistency"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("message_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("messages", sa.Column("sequence", sa.Integer(), nullable=True))
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY conversation_id
                       ORDER BY created_at, rowid
                   ) - 1 AS seq
            FROM messages
        )
        UPDATE messages
        SET sequence = (SELECT seq FROM ranked WHERE ranked.id = messages.id)
    """))
    op.execute(sa.text("""
        UPDATE conversations
        SET message_sequence = COALESCE(
            (SELECT MAX(sequence) + 1
             FROM messages
             WHERE messages.conversation_id = conversations.id),
            0
        )
    """))
    with op.batch_alter_table("messages") as batch_op:
        batch_op.alter_column("sequence", existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint(
            "uq_messages_conversation_sequence",
            ["conversation_id", "sequence"],
        )


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_constraint("uq_messages_conversation_sequence", type_="unique")
        batch_op.drop_column("sequence")
    op.drop_column("conversations", "message_sequence")
