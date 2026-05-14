"""Alert history model — tracks every alert fired and its delivery status."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class AlertHistory(Base):
    __tablename__ = "alert_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("listings.id"), nullable=False
    )
    deal_score: Mapped[float | None] = mapped_column(Numeric)
    preference_score: Mapped[float | None] = mapped_column(Numeric)
    composite_score: Mapped[float | None] = mapped_column(Numeric)
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    delivery_status: Mapped[str] = mapped_column(Text, default="sent")
    channel: Mapped[str] = mapped_column(Text, default="discord")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    listing = relationship("Listing", backref="alerts")
