"""initial

Revision ID: 001
Revises:
Create Date: 2026-06-30

Create the initial schema: files, blocks, shares tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("file_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("blocklist", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "modified_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "blocks",
        sa.Column("block_hash", sa.Text(), primary_key=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("ref_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "stored_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "shares",
        sa.Column("share_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.file_id"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("shared_with", sa.BigInteger(), nullable=False),
        sa.Column("access_type", sa.String(20), nullable=False, server_default="reader"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("file_id", "shared_with", name="uq_share_file_user"),
    )


def downgrade() -> None:
    op.drop_table("shares")
    op.drop_table("blocks")
    op.drop_table("files")
