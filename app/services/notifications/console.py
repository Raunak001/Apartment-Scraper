"""Console notification service — logs alerts to stdout for dev/test."""

from app.core.logging import get_logger
from app.services.notifications.base import AlertPayload, NotificationService

logger = get_logger(__name__)


class ConsoleNotificationService(NotificationService):

    def send(self, payload: AlertPayload) -> bool:
        logger.info(
            "alert_fired",
            title=payload.listing_title,
            url=payload.listing_url,
            price=payload.price,
            neighborhood=payload.neighborhood,
            bedrooms=payload.bedrooms,
            composite_score=payload.composite_score,
            deal_score=payload.deal_score,
            preference_score=payload.preference_score,
            reasoning=payload.reasoning,
        )
        return True

    def send_heartbeat(self, stats: dict) -> bool:
        logger.info("heartbeat", **stats)
        return True
