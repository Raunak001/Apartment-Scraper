"""Multi-user schema scaffold: users table, user_id FKs, user_preference_scores.

Revision ID: f2b3c4d5e6f7
Revises: e1a2b3c4d5e6
Create Date: 2026-05-14 00:00:01.000000
"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f2b3c4d5e6f7'
down_revision: Union[str, None] = 'e1a2b3c4d5e6'
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    # 1. Create users table
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("discord_webhook_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 2. Seed default user for backward compatibility
    op.execute(
        f"INSERT INTO users (id, email, display_name, is_active) "
        f"VALUES ('{DEFAULT_USER_ID}', 'default@localhost', 'Default User', true)"
    )

    # 3. Add nullable user_id FK to preferences
    op.add_column("preferences", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_preferences_user_id", "preferences", "users", ["user_id"], ["id"])
    op.execute(f"UPDATE preferences SET user_id = '{DEFAULT_USER_ID}'")

    # 4. Add nullable user_id FK to alert_history
    op.add_column("alert_history", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_alert_history_user_id", "alert_history", "users", ["user_id"], ["id"])
    op.execute(f"UPDATE alert_history SET user_id = '{DEFAULT_USER_ID}'")

    # 5. Create user_preference_scores table
    op.create_table(
        "user_preference_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("preference_score", sa.Numeric(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "listing_id", name="uq_user_listing_preference"),
    )


def downgrade() -> None:
    op.drop_table("user_preference_scores")

    op.drop_constraint("fk_alert_history_user_id", "alert_history", type_="foreignkey")
    op.drop_column("alert_history", "user_id")

    op.drop_constraint("fk_preferences_user_id", "preferences", type_="foreignkey")
    op.drop_column("preferences", "user_id")

    op.drop_table("users")
