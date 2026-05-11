"""add deal_score and rename price distribution columns to median/mad

Revision ID: a3f1b2c4d5e6
Revises: d8d871d6e6c0
Create Date: 2026-05-07 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f1b2c4d5e6'
down_revision: Union[str, None] = 'd8d871d6e6c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('deal_score', sa.Numeric(), nullable=True))
    op.alter_column('price_distributions', 'mean_price', new_column_name='median_price')
    op.alter_column('price_distributions', 'stddev_price', new_column_name='mad_price')
    op.create_index('ix_listings_neighborhood_bedrooms', 'listings', ['neighborhood', 'bedrooms'])


def downgrade() -> None:
    op.drop_index('ix_listings_neighborhood_bedrooms', table_name='listings')
    op.alter_column('price_distributions', 'mad_price', new_column_name='stddev_price')
    op.alter_column('price_distributions', 'median_price', new_column_name='mean_price')
    op.drop_column('listings', 'deal_score')
