# Apartment Rental Deal Scanner

A Chicago apartment rental deal scanner — a backend system that continuously scrapes listings from multiple sources, builds a statistical fair-market-value model per neighborhood, and fires Discord alerts for statistically underpriced listings ranked by a composite deal + personal-fit score.

**Primary goal:** Resume project targeting junior-to-mid backend SWE roles. Every architectural decision should be explainable in an interview and justify the tech choice.

---

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python | Scraping/data ecosystem, BeautifulSoup, Playwright |
| API | FastAPI | Async-native, fast to write |
| Job queue | Celery + Redis | Canonical async worker pattern for resumes |
| Database | PostgreSQL | Relational, Alembic migrations, SQLAlchemy ORM |
| LLM | Claude Haiku (`claude-haiku-4-5-20251001`) | Fast, cheap, used for dedup + enrichment |
| Containers | Docker Compose | Single-command deploy, all services |
| Deployment | AWS EC2 t3.small (~$15/mo) | AWS on resume, Docker Compose (not managed services yet) |
| Alerts | Discord webhook | Simple, personal, no extra infra |
| Monitoring | Flower (Celery dashboard) | Worker visibility |

---

## Data Sources

| Source | Method | Phase |
|---|---|---|
| Craigslist Chicago | BeautifulSoup + RSS | Phase 1 (start here) |
| Domu | BeautifulSoup | Phase 5 |
| Zillow | Gmail API / IMAP email parsing | Phase 5 |
| Apartments.com | Gmail API / IMAP email parsing | Phase 5 |

**Zillow + Apartments.com use email alert ingestion, not scraping.** Set up saved searches on both platforms → dedicated Gmail account receives alerts → Gmail API polls inbox → parse HTML email → normalize into unified listing model. This avoids their anti-bot infrastructure entirely.

**Facebook Marketplace is cut.** No email alerts, requires auth, actively litigated against.

---

## Pipeline Order

```
Scrape / Email Ingest
        ↓
   Normalize
   (unified listing model)
        ↓
Stage-1 Dedup (rule-based)
   - hash dedup within source
   - rapidfuzz on address + unit# across sources
   - cheaper listing kept; >10% price diff → manual flag
        ↓ (ambiguous pairs only)
Stage-2 LLM Dedup (Claude Haiku)
   - rate-limited, isolated queue
   - LRU result cache with TTL
        ↓ (confirmed new listings)
Enrichment Service (Claude Haiku)
   - amenity extraction from description
   - preference scoring vs preferences.yaml
   - token cost tracked per listing
        ↓
Scoring Engine
   - z-score vs neighborhood price distribution → deal_score
   - composite = deal×0.6 + preference×0.4 (configurable in yaml)
   - GATE: sample_count > 20 per neighborhood segment before alerting
        ↓
Alert Dispatcher
   - Redis sorted set priority queue (scored by composite)
   - staleness re-check before firing
   - 24h cooldown dedup per listing
   - NotificationService abstraction (Discord is one implementation)
        ↓
Discord Webhook
```

---

## Phase Plan

### Phase 1 — Scraping Foundation ← START HERE
- [ ] Docker Compose stack: FastAPI, Celery worker, Celery Beat, Redis, PostgreSQL
- [ ] SQLAlchemy models + Alembic first migration (see schema below)
- [ ] Craigslist Chicago scraper (BeautifulSoup + RSS)
- [ ] Raw listing storage with dedup (hash within source)
- [ ] FastAPI health + status endpoints
- [ ] Structured logging

**Phase 1 done when:** Craigslist listings are flowing into PostgreSQL cleanly, no duplicates, visible in logs.

### Phase 2 — EnrichmentService
- [ ] Load preferences.yaml into DB on startup
- [ ] Claude Haiku amenity extraction prompt
- [ ] Claude Haiku preference scoring prompt
- [ ] EnrichmentService worker (separate Celery queue)
- [ ] Token cost tracking per listing
- [ ] NotificationService abstraction + Discord implementation

**Phase 2 done when:** Every new Craigslist listing has enrichment_results populated and preference_score attached.

### Phase 3 — Price Model
- [ ] Rolling price distribution per neighborhood × bedroom_count
- [ ] Z-score deal scoring
- [ ] sample_count > 20 gate (hard gate, no exceptions)
- [ ] price_distributions table populated and updating

**Phase 3 done when:** New listings get a meaningful deal_score and the gate prevents noisy alerts.

### Phase 4 — Alert Pipeline
- [ ] Redis sorted set priority queue
- [ ] Alert Dispatcher worker
- [ ] Composite score = deal×0.6 + preference×0.4
- [ ] Discord webhook alert with listing details
- [ ] 24h cooldown dedup
- [ ] Staleness re-check before firing
- [ ] Daily heartbeat Discord message (scrape counts per source, last run times)

**Phase 4 done when:** Real alerts fire on Discord with composite scores. Heartbeat message arrives daily.

### Phase 5 — Multi-source Expansion
- [ ] Domu scraper (BeautifulSoup)
- [ ] Gmail API client + IMAP polling worker
- [ ] Zillow email parser
- [ ] Apartments.com email parser
- [ ] Playwright headless for any JS-rendered sources
- [ ] Fan-out scraping: each source on independent Celery schedule
- [ ] Cross-source Stage-1 dedup active

**Note:** Do NOT run Playwright until t3.small memory is validated — consider t3.medium ($30/mo, 4GB RAM) before this phase.

### Phase 6 — Polish
- [ ] Listing TTL + staleness detection job
- [ ] nightly pg_dump → S3 backup (cron in container)
- [ ] Architecture diagram in README
- [ ] README with setup instructions + demo screenshots
- [ ] Multi-user schema scaffold (no auth yet — just design DB for it)

---

## Database Schema

### listings
```sql
id              UUID PRIMARY KEY
external_id     TEXT NOT NULL          -- source's own ID
source          TEXT NOT NULL          -- craigslist, domu, zillow, apartments_com
url             TEXT NOT NULL
title           TEXT
address         TEXT
unit_number     TEXT
neighborhood    TEXT
city            TEXT DEFAULT 'chicago'
price           INTEGER                -- monthly rent in dollars
bedrooms        INTEGER
bathrooms       NUMERIC
sqft            INTEGER
description     TEXT
status          TEXT DEFAULT 'active'  -- active | stale | gone | duplicate
listed_at       TIMESTAMP
scraped_at      TIMESTAMP DEFAULT NOW()
last_checked_at TIMESTAMP
```

### price_distributions
```sql
id              UUID PRIMARY KEY
neighborhood    TEXT NOT NULL
bedroom_count   INTEGER NOT NULL
mean_price      NUMERIC
stddev_price    NUMERIC
sample_count    INTEGER DEFAULT 0
last_updated    TIMESTAMP
UNIQUE(neighborhood, bedroom_count)
```

### enrichment_results
```sql
id              UUID PRIMARY KEY
listing_id      UUID REFERENCES listings(id)
amenities       JSONB                  -- extracted structured amenities
preference_score NUMERIC               -- 0.0 to 1.0
llm_notes       TEXT                  -- LLM's plain-language reasoning
tokens_used     INTEGER
enriched_at     TIMESTAMP
```

### preferences
```sql
id              UUID PRIMARY KEY
version         INTEGER DEFAULT 1
config          JSONB                  -- full preferences.yaml contents
active          BOOLEAN DEFAULT TRUE
loaded_at       TIMESTAMP
```

### alert_history
```sql
id              UUID PRIMARY KEY
listing_id      UUID REFERENCES listings(id)
deal_score      NUMERIC
preference_score NUMERIC
composite_score NUMERIC
fired_at        TIMESTAMP
delivery_status TEXT                  -- sent | failed
channel         TEXT DEFAULT 'discord'
```

---

## preferences.yaml (source of truth)

This file lives at the repo root. Loaded into PostgreSQL on startup. Edit and restart to update preferences — no DB migration needed.

```yaml
search:
  target_city: chicago
  neighborhoods:
    - lincoln_park
    - wicker_park
    - logan_square
    - bucktown
    - roscoe_village

pricing:
  max_price: 2200          # monthly rent ceiling in dollars
  alert_threshold: 0.15   # min fraction below neighborhood median to alert

unit:
  min_bedrooms: 1
  max_bedrooms: 2
  min_sqft: null           # optional

amenities:
  required: []             # e.g. [parking, in_unit_laundry]
  preferred: []            # e.g. [exposed_brick, roof_deck]
  dealbreakers: []         # e.g. [no_pets, carpet_only]

fit:
  vibe: >
    Vibrant but not loud. Prefer vintage buildings with character over
    new glass construction. Near Blue or Brown line, max 10 min walk.
  transit: [blue_line, brown_line]
  building_type: vintage   # vintage | modern | no_preference

scoring:
  weights:
    deal: 0.6
    preference: 0.4
  min_composite_score: 0.55

alerts:
  cooldown_hours: 24
  max_per_day: 10
```

---

## Key Constraints (do not violate)

1. **Phase gate rule**: Each phase must be fully working before starting the next. No exceptions.
2. **sample_count > 20 gate**: Never fire an alert if the neighborhood segment has fewer than 20 listings in the price model. Noisy early alerts undermine trust in the system.
3. **Composite weights are configurable**: Never hardcode 0.6/0.4. Always read from preferences.yaml / DB.
4. **NotificationService abstraction**: Discord is one implementation. The dispatcher calls `notification_service.send(alert)`, not `discord.send(alert)` directly.
5. **Craigslist first**: Don't touch Domu or email ingestion until Craigslist is clean and Phase 1 is gated.
6. **No Playwright in Phase 1-4**: BeautifulSoup only until memory constraints on EC2 are validated.
7. **t3.small RAM**: 2GB total. Playwright spikes 400-600MB. Don't run it until Phase 5 with a possible upgrade to t3.medium.

---

## Decisions Already Made (don't re-litigate)

- **No managed RDS or ElastiCache** — PostgreSQL and Redis run in Docker containers on EC2. Migrate to managed services in a later phase if needed. Migration story is itself a resume talking point.
- **No frontend dashboard** — Discord alerts + FastAPI REST (Postman-accessible) is sufficient. Dashboard is a future phase.
- **Single-user for now** — Schema should be designed with multi-user in mind (don't hardcode user-specific data without a user_id FK) but no auth layer yet.
- **Email ingestion, not scraping for Zillow/Apartments.com** — Anti-bot measures make scraping unreliable. Gmail API approach is more robust.
- **Facebook Marketplace cut** — No email alerts, requires auth, actively litigated against by Meta.

---

## Resume Angle (keep this in mind when writing code)

Every component should generate a defensible interview talking point:

- **Celery + Redis** → "async distributed job queue with fan-out ingestion"
- **Two-stage dedup** → "rule-based fast path handles 95% of cases; LLM disambiguation on ambiguous near-duplicates"
- **EnrichmentService** → "LLM pipeline extracting structured amenity features + preference scores from unstructured descriptions"
- **Scoring engine** → "z-score anomaly detection against rolling neighborhood price distributions"
- **Alert dispatcher** → "priority-ranked alert dispatch via Redis sorted sets with composite scoring"
- **Docker Compose** → "containerized full stack, single-command deployment on AWS EC2"

When in doubt, write code that can be described clearly in one sentence. If you can't describe what a function does in one sentence, it's doing too much.
