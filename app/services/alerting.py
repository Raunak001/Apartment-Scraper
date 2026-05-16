"""Alert dispatching service — composite scoring, gate checks, and alert firing."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_notification_service
from app.core.logging import get_logger
from app.models.alert import AlertHistory
from app.models.enrichment import EnrichmentResult
from app.models.listing import Listing
from app.models.price_distribution import PriceDistribution
from app.services.notifications.base import AlertPayload

logger = get_logger(__name__)


class AlertDispatcher:

    def compute_composite(
        self, deal_score: float, preference_score: float, weights: dict
    ) -> float:
        w_deal = weights.get("deal", 0.6)
        w_pref = weights.get("preference", 0.4)
        return round(deal_score * w_deal + preference_score * w_pref, 4)

    def passes_alert_threshold(
        self, db: Session, listing: Listing, alert_threshold: float
    ) -> bool:
        if listing.price is None or listing.neighborhood is None or listing.bedrooms is None:
            return False

        dist = db.execute(
            select(PriceDistribution).where(
                PriceDistribution.neighborhood == listing.neighborhood,
                PriceDistribution.bedroom_count == listing.bedrooms,
            )
        ).scalar_one_or_none()

        if dist is None or dist.median_price is None:
            return False

        threshold_price = float(dist.median_price) * (1 - alert_threshold)
        return listing.price <= threshold_price

    def is_on_cooldown(
        self, db: Session, listing_id: uuid.UUID, cooldown_hours: int, url: str | None = None
    ) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)

        # Check by listing_id first (fast path)
        count = db.execute(
            select(func.count(AlertHistory.id)).where(
                AlertHistory.listing_id == listing_id,
                AlertHistory.fired_at >= cutoff,
            )
        ).scalar() or 0
        if count > 0:
            return True

        # Also block if any listing with the same URL fired recently — catches
        # reposts of the same apartment that got separate DB rows
        if url:
            count = db.execute(
                select(func.count(AlertHistory.id))
                .join(Listing, AlertHistory.listing_id == Listing.id)
                .where(
                    Listing.url == url,
                    AlertHistory.fired_at >= cutoff,
                )
            ).scalar() or 0
            if count > 0:
                return True

        return False

    def alerts_fired_in_window(self, db: Session) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        return db.execute(
            select(func.count(AlertHistory.id)).where(AlertHistory.fired_at >= cutoff)
        ).scalar() or 0

    def build_payload(
        self, listing: Listing, enrichment: EnrichmentResult, composite: float
    ) -> AlertPayload:
        reasoning = enrichment.llm_notes or ""
        if len(reasoning) > 200:
            reasoning = reasoning[:197] + "..."

        amenities_summary = "N/A"
        if enrichment.amenities and isinstance(enrichment.amenities, dict):
            parts = [f"{k}: {v}" for k, v in enrichment.amenities.items() if v]
            amenities_summary = " | ".join(parts) if parts else "N/A"

        return AlertPayload(
            listing_title=listing.title or "Untitled",
            listing_url=listing.url,
            price=listing.price or 0,
            neighborhood=listing.neighborhood or "Unknown",
            bedrooms=listing.bedrooms or 0,
            composite_score=composite,
            deal_score=float(listing.deal_score) if listing.deal_score is not None else 0.0,
            preference_score=float(enrichment.preference_score) if enrichment.preference_score is not None else 0.0,
            reasoning=reasoning,
            amenities_summary=amenities_summary,
            source=listing.source or "",
        )

    def fire_alert(
        self, db: Session, listing: Listing, enrichment: EnrichmentResult, composite: float
    ) -> bool:
        payload = self.build_payload(listing, enrichment, composite)
        service = get_notification_service()
        channel = "discord" if hasattr(service, "webhook_url") else "console"

        success = service.send(payload)

        alert = AlertHistory(
            listing_id=listing.id,
            deal_score=float(listing.deal_score) if listing.deal_score is not None else None,
            preference_score=float(enrichment.preference_score) if enrichment.preference_score is not None else None,
            composite_score=composite,
            delivery_status="sent" if success else "failed",
            channel=channel,
        )
        db.add(alert)
        db.commit()

        logger.info(
            "alert_fired",
            listing_id=str(listing.id),
            composite_score=composite,
            delivery_status=alert.delivery_status,
            channel=channel,
        )
        return success

    def backfill_composite_scores(self, db: Session, weights: dict) -> dict:
        listings = (
            db.query(Listing, EnrichmentResult)
            .join(EnrichmentResult, Listing.id == EnrichmentResult.listing_id)
            .filter(
                Listing.deal_score.isnot(None),
                EnrichmentResult.preference_score.isnot(None),
                EnrichmentResult.skipped.is_(False),
                EnrichmentResult.failed.is_(False),
            )
            .all()
        )

        updated = 0
        for listing, enrichment in listings:
            composite = self.compute_composite(
                float(listing.deal_score), float(enrichment.preference_score), weights
            )
            if listing.composite_score is None or round(float(listing.composite_score), 4) != composite:
                listing.composite_score = composite
                updated += 1

        db.commit()
        logger.info("backfill_composite_scores_complete", updated=updated, total=len(listings))
        return {"updated": updated, "total": len(listings)}

    def dispatch(self, db: Session, preferences: dict) -> dict:
        weights = preferences.get("scoring", {}).get("weights", {"deal": 0.6, "preference": 0.4})
        alert_threshold = preferences.get("pricing", {}).get("alert_threshold", 0.15)
        min_composite = preferences.get("scoring", {}).get("min_composite_score", 0.55)
        cooldown_hours = preferences.get("alerts", {}).get("cooldown_hours", 24)
        max_per_day = preferences.get("alerts", {}).get("max_per_day", 100)

        fired_count = self.alerts_fired_in_window(db)
        if fired_count >= max_per_day:
            logger.info("dispatch_rate_limited", fired_today=fired_count, max_per_day=max_per_day)
            return {"alerts_fired": 0, "skipped_threshold": 0, "skipped_cooldown": 0, "rate_limited": True}

        candidates = (
            db.query(Listing, EnrichmentResult)
            .join(EnrichmentResult, Listing.id == EnrichmentResult.listing_id)
            .filter(
                Listing.status == "active",
                Listing.deal_score.isnot(None),
                Listing.composite_score.isnot(None),
                Listing.composite_score >= min_composite,
                EnrichmentResult.skipped.is_(False),
                EnrichmentResult.failed.is_(False),
            )
            .order_by(Listing.composite_score.desc())
            .limit(50)
            .all()
        )

        alerts_fired = 0
        skipped_threshold = 0
        skipped_cooldown = 0

        for listing, enrichment in candidates:
            if not self.passes_alert_threshold(db, listing, alert_threshold):
                skipped_threshold += 1
                continue

            if self.is_on_cooldown(db, listing.id, cooldown_hours, url=listing.url):
                skipped_cooldown += 1
                continue

            if self.alerts_fired_in_window(db) >= max_per_day:
                logger.info("dispatch_hit_rate_limit_mid_batch", fired=alerts_fired)
                break

            self.fire_alert(db, listing, enrichment, float(listing.composite_score))
            alerts_fired += 1

        logger.info(
            "dispatch_complete",
            alerts_fired=alerts_fired,
            skipped_threshold=skipped_threshold,
            skipped_cooldown=skipped_cooldown,
            candidates=len(candidates),
        )
        return {
            "alerts_fired": alerts_fired,
            "skipped_threshold": skipped_threshold,
            "skipped_cooldown": skipped_cooldown,
            "rate_limited": False,
        }

    def evaluate_single(self, db: Session, listing_id: str, preferences: dict) -> dict:
        weights = preferences.get("scoring", {}).get("weights", {"deal": 0.6, "preference": 0.4})
        alert_threshold = preferences.get("pricing", {}).get("alert_threshold", 0.15)
        min_composite = preferences.get("scoring", {}).get("min_composite_score", 0.55)
        cooldown_hours = preferences.get("alerts", {}).get("cooldown_hours", 24)
        max_per_day = preferences.get("alerts", {}).get("max_per_day", 100)

        listing = db.query(Listing).filter(Listing.id == listing_id).first()
        if not listing or listing.status != "active":
            return {"status": "skipped", "reason": "not_found_or_inactive"}

        if listing.deal_score is None:
            return {"status": "skipped", "reason": "no_deal_score"}

        enrichment = (
            db.query(EnrichmentResult)
            .filter(
                EnrichmentResult.listing_id == listing.id,
                EnrichmentResult.skipped.is_(False),
                EnrichmentResult.failed.is_(False),
                EnrichmentResult.preference_score.isnot(None),
            )
            .first()
        )
        if not enrichment:
            return {"status": "skipped", "reason": "no_enrichment"}

        composite = self.compute_composite(
            float(listing.deal_score), float(enrichment.preference_score), weights
        )
        listing.composite_score = composite
        db.commit()

        if composite < min_composite:
            return {"status": "skipped", "reason": "below_min_composite", "composite_score": composite}

        if not self.passes_alert_threshold(db, listing, alert_threshold):
            return {"status": "skipped", "reason": "below_alert_threshold", "composite_score": composite}

        if self.is_on_cooldown(db, listing.id, cooldown_hours, url=listing.url):
            return {"status": "skipped", "reason": "on_cooldown", "composite_score": composite}

        if self.alerts_fired_in_window(db) >= max_per_day:
            return {"status": "skipped", "reason": "rate_limited", "composite_score": composite}

        self.fire_alert(db, listing, enrichment, composite)
        return {"status": "alert_fired", "composite_score": composite}

    def gather_heartbeat_stats(self, db: Session) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        total_scraped = db.execute(
            select(func.count(Listing.id)).where(Listing.scraped_at >= cutoff)
        ).scalar() or 0

        total_enriched = db.execute(
            select(func.count(EnrichmentResult.id)).where(EnrichmentResult.enriched_at >= cutoff)
        ).scalar() or 0

        alerts_fired = db.execute(
            select(func.count(AlertHistory.id)).where(AlertHistory.fired_at >= cutoff)
        ).scalar() or 0

        tokens_today = db.execute(
            select(func.coalesce(func.sum(EnrichmentResult.tokens_used), 0)).where(
                EnrichmentResult.enriched_at >= cutoff
            )
        ).scalar() or 0

        active_listings = db.execute(
            select(func.count(Listing.id)).where(Listing.status == "active")
        ).scalar() or 0

        segments_meeting_gate = db.execute(
            select(func.count(PriceDistribution.id)).where(
                PriceDistribution.sample_count >= 5
            )
        ).scalar() or 0

        return {
            "total_scraped": total_scraped,
            "total_enriched": total_enriched,
            "alerts_fired": alerts_fired,
            "tokens_today": tokens_today,
            "active_listings": active_listings,
            "segments_meeting_gate": segments_meeting_gate,
        }
