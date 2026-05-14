"""add_dedup_hash_and_dedup_pairs

Revision ID: c5f3a8b9d2e7
Revises: b4e2f7a8c9d1
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'c5f3a8b9d2e7'
down_revision: Union[str, None] = 'b4e2f7a8c9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add dedup_hash column to listings
    op.add_column('listings', sa.Column('dedup_hash', sa.Text(), nullable=True))
    op.create_index('ix_listings_dedup_hash', 'listings', ['dedup_hash'])

    # Create dedup_pairs table
    op.create_table(
        'dedup_pairs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('listing_a_id', UUID(as_uuid=True), sa.ForeignKey('listings.id'), nullable=False),
        sa.Column('listing_b_id', UUID(as_uuid=True), sa.ForeignKey('listings.id'), nullable=False),
        sa.Column('similarity_score', sa.Numeric(), nullable=False),
        sa.Column('price_diff_pct', sa.Numeric(), nullable=False),
        sa.Column('status', sa.Text(), server_default='pending'),
        sa.Column('resolution', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_dedup_pairs_status', 'dedup_pairs', ['status'])


def downgrade() -> None:
    op.drop_table('dedup_pairs')
    op.drop_index('ix_listings_dedup_hash', table_name='listings')
    op.drop_column('listings', 'dedup_hash')
