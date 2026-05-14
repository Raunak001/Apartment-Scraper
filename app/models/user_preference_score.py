"""UserPreferenceScore model — per-user subjective preference scores.

Scaffolds the separation of per-user preference scoring from shared
enrichment data. In multi-user mode, enrichment_results holds objective
amenity data while this table holds the subjective preference_score
computed against each user's preferences.yaml config.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserPreferenceScore(Base):
    __tablename__ = "user_preference_scores"

    __table_args__ = (
        UniqueConstraint("user_id", "listing_id", name="uq_user_listing_preference"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("listings.id"), nullable=False
    )
    preference_score: Mapped[float | None] = mapped_column(Numeric)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<UserPreferenceScore user={self.user_id} listing={self.listing_id} score={self.preference_score}>"
