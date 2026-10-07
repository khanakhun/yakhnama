"""fix(media): index the uploads still waiting for their file.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-05T00:00:00+00:00

Schema migration only; no data is written here.

Adds the partial index ``ix_media_assets_created_at_requested`` on ``created_at``
over assets whose ``upload_status`` is ``requested``, which serves the periodic
stale-upload sweep (``media.sweep_stale_uploads``) without scanning completed media.

Downgrade drops the index.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the partial index."""
    op.create_index(
        "ix_media_assets_created_at_requested",
        "media_assets",
        ["created_at"],
        postgresql_where=sa.text("upload_status = 'requested'"),
    )


def downgrade() -> None:
    """Drop the partial index."""
    op.drop_index("ix_media_assets_created_at_requested", table_name="media_assets")
