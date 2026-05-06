"""Enrichment results model — LLM-extracted amenities and preference scores."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class EnrichmentResult(Base):
    __tablename__ = "enrichment_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("listings.id"), nullable=False, unique=True
    )
    amenities: Mapped[dict | None] = mapped_column(JSONB)
    preference_score: Mapped[float | None] = mapped_column(Numeric)
    llm_notes: Mapped[str | None] = mapped_column(Text)
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    listing = relationship("Listing", backref="enrichment")
