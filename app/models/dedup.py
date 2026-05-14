"""DedupPair model — tracks cross-source duplicate candidates for review."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class DedupPair(Base):
    __tablename__ = "dedup_pairs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    listing_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("listings.id"), nullable=False
    )
    listing_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("listings.id"), nullable=False
    )
    similarity_score: Mapped[float] = mapped_column(Numeric, nullable=False)
    price_diff_pct: Mapped[float] = mapped_column(Numeric, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending")
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<DedupPair {self.listing_a_id} <-> {self.listing_b_id} score={self.similarity_score} status={self.status}>"
