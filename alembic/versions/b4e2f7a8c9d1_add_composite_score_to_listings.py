"""add composite_score to listings

Revision ID: b4e2f7a8c9d1
Revises: a3f1b2c4d5e6
Create Date: 2026-05-10 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4e2f7a8c9d1'
down_revision: Union[str, None] = 'a3f1b2c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('composite_score', sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column('listings', 'composite_score')
