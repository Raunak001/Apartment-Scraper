"""Manual test script to verify Discord webhook and console notification."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import DISCORD_WEBHOOK_URL
from app.services.notifications import (
    AlertPayload,
    ConsoleNotificationService,
    DiscordNotificationService,
)

SAMPLE_PAYLOAD = AlertPayload(
    listing_title="Sunny 1BR in Lincoln Park - In-Unit Laundry!",
    listing_url="https://chicago.craigslist.org/chc/apa/d/example/1234567890.html",
    price=1650,
    neighborhood="lincoln_park",
    bedrooms=1,
    composite_score=0.78,
    deal_score=0.82,
    preference_score=0.72,
    reasoning="Strong price-to-value ratio with in-unit laundry matching required amenity. "
    "Modern building near Brown Line.",
    amenities_summary="Laundry: in-unit | Dishwasher: yes | Parking: street | AC: central | Pets: cats ok",
)


def main():
    print("=== Testing Console Notification ===")
    console = ConsoleNotificationService()
    result = console.send(SAMPLE_PAYLOAD)
    print(f"Console send result: {result}\n")

    result = console.send_heartbeat({"total_scraped": 45, "total_enriched": 38, "alerts_fired": 3, "tokens_today": 12500})
    print(f"Console heartbeat result: {result}\n")

    if DISCORD_WEBHOOK_URL:
        print("=== Testing Discord Notification ===")
        discord = DiscordNotificationService()
        result = discord.send(SAMPLE_PAYLOAD)
        print(f"Discord send result: {result}")

        result = discord.send_heartbeat({"total_scraped": 45, "total_enriched": 38, "alerts_fired": 3, "tokens_today": 12500})
        print(f"Discord heartbeat result: {result}")
    else:
        print("=== Discord Skipped (no DISCORD_WEBHOOK_URL in .env) ===")
        print("Set DISCORD_WEBHOOK_URL in .env to test Discord delivery.")


if __name__ == "__main__":
    main()
