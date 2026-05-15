"""Discord webhook notification implementation."""

import httpx

from app.core.config import DISCORD_WEBHOOK_URL
from app.core.logging import get_logger
from app.services.notifications.base import AlertPayload, NotificationService

logger = get_logger(__name__)


class DiscordNotificationService(NotificationService):

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or DISCORD_WEBHOOK_URL

    def send(self, payload: AlertPayload) -> bool:
        embed = self._build_embed(payload)
        return self._post({"embeds": [embed]})

    def send_heartbeat(self, stats: dict) -> bool:
        lines = [
            "**Daily Heartbeat**",
            f"Listings scraped: {stats.get('total_scraped', 'N/A')}",
            f"Listings enriched: {stats.get('total_enriched', 'N/A')}",
            f"Alerts fired: {stats.get('alerts_fired', 'N/A')}",
            f"Tokens used today: {stats.get('tokens_today', 'N/A')}",
        ]
        return self._post({"content": "\n".join(lines)})

    def _build_embed(self, payload: AlertPayload) -> dict:
        color = self._score_color(payload.composite_score)
        return {
            "title": f"🏠 {payload.listing_title}",
            "url": payload.listing_url,
            "color": color,
            "fields": [
                {"name": "Price", "value": f"${payload.price}/mo", "inline": True},
                {"name": "Neighborhood", "value": payload.neighborhood, "inline": True},
                {"name": "Bedrooms", "value": str(payload.bedrooms), "inline": True},
                {"name": "Source", "value": payload.source.replace("_", " ").title() if payload.source else "Unknown", "inline": True},
                {"name": "Composite Score", "value": f"{payload.composite_score:.2f}", "inline": True},
                {"name": "Deal Score", "value": f"{payload.deal_score:.2f}", "inline": True},
                {"name": "Preference Score", "value": f"{payload.preference_score:.2f}", "inline": True},
                {"name": "Why", "value": payload.reasoning, "inline": False},
                {"name": "Amenities", "value": payload.amenities_summary, "inline": False},
            ],
        }

    def _score_color(self, score: float) -> int:
        if score >= 0.8:
            return 0x00FF00  # green
        if score >= 0.6:
            return 0xFFFF00  # yellow
        return 0xFF8C00  # orange

    def _post(self, data: dict) -> bool:
        if not self.webhook_url:
            logger.warning("discord_webhook_not_configured")
            return False
        try:
            resp = httpx.post(self.webhook_url, json=data, timeout=10.0)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error("discord_send_failed", error=str(e))
            return False
