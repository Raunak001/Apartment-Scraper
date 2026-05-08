"""Notification service abstraction — send alerts via any channel."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AlertPayload:
    listing_title: str
    listing_url: str
    price: int
    neighborhood: str
    bedrooms: int
    composite_score: float
    deal_score: float
    preference_score: float
    reasoning: str
    amenities_summary: str


class NotificationService(ABC):

    @abstractmethod
    def send(self, payload: AlertPayload) -> bool:
        """Send an alert. Returns True on success."""
        ...

    @abstractmethod
    def send_heartbeat(self, stats: dict) -> bool:
        """Send a daily heartbeat summary. Returns True on success."""
        ...
