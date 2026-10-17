"""documents.was_encrypted: ING-10 never deduplicates against password-protected documents.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("was_encrypted", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("documents", "was_encrypted")
