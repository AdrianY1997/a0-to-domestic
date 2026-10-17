"""Initial schema (SPEC 11.1): documents, jobs, printers, presets, app_settings.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("format", sa.String(32)),
        sa.Column("mime", sa.String(128)),
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="processing"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_detail", sa.Text),
        sa.Column("error_params_json", sa.Text),
        sa.Column("pages_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("warnings_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_created_at", "documents", ["created_at"])
    op.create_index("ix_documents_expires_at", "documents", ["expires_at"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(64)),
        sa.Column("progress", sa.Float, nullable=False, server_default="0"),
        sa.Column("settings_json", sa.Text),
        sa.Column("options_json", sa.Text),
        sa.Column("plans_json", sa.Text),
        sa.Column("outputs_json", sa.Text),
        sa.Column("warnings_json", sa.Text),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_detail", sa.Text),
        sa.Column("error_params_json", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("expires_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_jobs_document_id", "jobs", ["document_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_index("ix_jobs_expires_at", "jobs", ["expires_at"])
    op.create_index("ix_jobs_document_kind", "jobs", ["document_id", "kind"])

    op.create_table(
        "printers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="generic"),
        sa.Column("builtin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("papers_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("default_margins_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("color", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("scale_x", sa.Float, nullable=False, server_default="1"),
        sa.Column("scale_y", sa.Float, nullable=False, server_default="1"),
        sa.Column("ipp_uri", sa.Text),
        sa.Column("defaults_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "presets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("builtin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("settings_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value_json", sa.Text, nullable=False, server_default="null"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("presets")
    op.drop_table("printers")
    op.drop_table("jobs")
    op.drop_table("documents")
