"""Backfill last_checked_at with scraped_at for existing listings.

Revision ID: e1a2b3c4d5e6
Revises: c5f3a8b9d2e7
Create Date: 2026-05-14 00:00:00.000000
"""

from typing import Union

from alembic import op

revision: str = 'e1a2b3c4d5e6'
down_revision: Union[str, None] = 'c5f3a8b9d2e7'
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE listings SET last_checked_at = scraped_at WHERE last_checked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("UPDATE listings SET last_checked_at = NULL")
