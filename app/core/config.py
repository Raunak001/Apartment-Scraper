"""Application configuration loaded from .env and preferences.yaml."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

load_dotenv(PROJECT_ROOT / ".env", override=True)
PREFERENCES_PATH = PROJECT_ROOT / "preferences.yaml"

# Database
DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/apartment_scraper")

# Redis
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Celery
CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# Scraping
SCRAPE_DELAY_SECONDS: float = float(os.getenv("SCRAPE_DELAY_SECONDS", "1.5"))
USER_AGENT: str = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# Logging
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# Anthropic (Enrichment)
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Discord (Notifications)
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")


def load_preferences() -> dict:
    """Load preferences.yaml from project root. Returns the parsed dict."""
    with open(PREFERENCES_PATH, "r") as f:
        return yaml.safe_load(f)
