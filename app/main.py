"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import load_preferences
from app.core.database import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.models.preference import Preference

setup_logging()
logger = get_logger(__name__)


def _load_preferences_to_db() -> None:
    """Load preferences.yaml into the preferences table on startup.

    Deactivates any previously active preferences and inserts the current
    config as a new versioned row.
    """
    db = SessionLocal()
    try:
        config = load_preferences()

        # Get the current max version
        latest = (
            db.query(Preference)
            .order_by(Preference.version.desc())
            .first()
        )
        next_version = (latest.version + 1) if latest else 1

        # Check if config actually changed
        if latest and latest.config == config:
            logger.info("preferences_unchanged", version=latest.version)
            return

        # Deactivate old preferences
        db.query(Preference).filter(Preference.active.is_(True)).update({"active": False})

        # Insert new version
        pref = Preference(version=next_version, config=config, active=True)
        db.add(pref)
        db.commit()
        logger.info("preferences_loaded", version=next_version)

    except Exception as e:
        db.rollback()
        logger.error("preferences_load_failed", error=str(e))
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("app_starting")
    _load_preferences_to_db()
    logger.info("app_ready")
    yield
    logger.info("app_shutting_down")


app = FastAPI(
    title="Apartment Scraper",
    description="Chicago apartment rental deal scanner",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
