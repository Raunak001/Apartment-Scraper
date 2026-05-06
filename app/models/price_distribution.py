"""Price distribution model — rolling stats per neighborhood × bedroom count."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PriceDistribution(Base):
    __tablename__ = "price_distributions"

    __table_args__ = (
        UniqueConstraint("neighborhood", "bedroom_count", name="uq_neighborhood_bedrooms"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    neighborhood: Mapped[str] = mapped_column(Text, nullable=False)
    bedroom_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_price: Mapped[float | None] = mapped_column(Numeric)
    stddev_price: Mapped[float | None] = mapped_column(Numeric)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
