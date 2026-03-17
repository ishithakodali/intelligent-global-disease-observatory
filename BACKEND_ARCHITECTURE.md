# Backend Architecture - Intelligent Global Disease Observatory

## 1. System Goal
This backend provides a real-time disease observability API that aggregates epidemiology, outbreak alerts, genomics, and therapeutic signals from multiple external sources, then normalizes and serves them through a single FastAPI service.

The architecture is designed for hackathon judging priorities:
- Required source integration and surveillance fidelity
- Robust backend system architecture
- Scientific traceability and source provenance

## 2. Technology Stack
- Language: Python 3.13
- API framework: FastAPI
- HTTP client: httpx (async)
- Database: SQLite (async via aiosqlite)
- Cache: in-memory TTL cache + optional Redis (distributed cache)
- Retry policy: tenacity exponential backoff
- Real-time channel: WebSocket for alert push

## 3. High-Level Components
- API Layer: `backend/main.py`
  - REST endpoints for observatory, catalog, profiles, history, compliance, and operations
  - WebSocket endpoint for live alerts
  - Background tasks for cache warmup and alert ingestion
- Orchestration Layer: `backend/services.py`
  - Builds unified observatory response
  - Applies source applicability logic and provenance labeling
  - Aggregates analytics and confidence metadata
- Profile Enrichment Layer: `backend/profile_service.py`
  - Builds and persists disease-level profile records
  - Bulk backfill for MalaCards disease names
- Providers: `backend/providers/*`
  - Integrations for epidemiology, genomics, therapy, outbreak feeds, and reference knowledge sources
- Persistence Layer: `backend/db.py`
  - Async schema and CRUD helpers for query history, alert events, MalaCards diseases, and disease profiles

## 4. Required Source Integrations
The backend integrates and reports these required or expected surveillance sources:

Epidemiology:
- WHO GHO OData
- Disease.sh
- CDC FluView-style endpoint
- ECDC open dataset endpoint

Outbreak feeds:
- WHO DON RSS
- ProMED RSS
- HealthMap RSS

Genomics:
- Open Targets GraphQL

Therapeutics:
- PubChem PUG-REST

Reference and profile context:
- NLM ClinicalTables ICD search
- Wikipedia summary API
- Wikidata entity search
- ClinicalTrials.gov API v2
- MedlinePlus query service
- OpenAlex works search

## 5. Data Flow
### 5.1 Observatory Request Flow
1. Client calls `/api/v1/observatory?disease=...&region=...`.
2. Service checks local/Redis cache.
3. Disease is resolved through ICD lookup if not in curated catalog.
4. Parallel enrichment is performed:
   - Epidemiology signal set (WHO, Disease.sh, CDC, ECDC)
   - Gene evidence (Open Targets)
   - Therapy hint (PubChem)
   - Outbreak alert merge (persisted alerts + live RSS feeds)
5. Response is normalized into typed model:
   - Classification, epidemiology, genes, therapeutics, analytics
   - Per-source diagnostics
   - Provenance summary
6. Query metadata is persisted to SQLite.
7. Response is cached and returned.

### 5.2 Alert Ingestion Flow
1. Background loop polls WHO DON, ProMED, and HealthMap at configured interval.
2. New alerts are deduplicated and persisted in `alert_events`.
3. Newly inserted alerts are broadcast to connected WebSocket clients (`/api/v1/ws/alerts`).

### 5.3 Disease Profile Backfill Flow
1. MalaCards disease names are imported (name list).
2. Backfill endpoint processes unhydrated names in batches.
3. Each disease triggers multi-source profile fetch and normalization.
4. Profile JSON is stored in `disease_profiles` for fast retrieval.

## 6. Persistence Model
SQLite tables:
- `query_history`: request trace and confidence metrics
- `malacards_diseases`: imported disease name set
- `alert_events`: deduplicated outbreak alerts with timestamps
- `disease_profiles`: enriched per-disease profile JSON and source quality

## 7. Caching Strategy
- Primary: process-local TTL cache for low-latency hot paths
- Optional: Redis cache for persistence across restarts/workers
- Cache clear endpoint: `/api/v1/ops/cache/clear`

## 8. Reliability and Resilience
- Async I/O end-to-end for database and HTTP calls
- Exponential retry for transient external failures
- Source-level fallback handling (no silent failures)
- Source diagnostics included in responses
- Rate limiting middleware for API protection

## 9. Scientific Traceability
Each observatory response includes:
- `source_status[]` with source, status, records, latency, applicability, and provenance
- `provenance_summary` for quick interpretation of live vs fallback vs cached evidence
- Compliance report endpoint (`/api/v1/compliance/report`) for judge-facing matrix and freshness snapshots

## 10. Backend Endpoints (Submission-Relevant)
Core:
- `/api/v1/observatory`
- `/api/v1/observatory/batch`
- `/api/v1/health`
- `/api/v1/compliance/report`

Catalog/Profile:
- `/api/v1/ailments/catalog`
- `/api/v1/malacards/import`
- `/api/v1/disease/profile`
- `/api/v1/disease/profile/backfill`
- `/api/v1/disease/profile/stats`

Realtime/Operations:
- `/api/v1/alerts/recent`
- `/api/v1/ws/alerts`
- `/api/v1/ops/cache/clear`

## 11. Non-Functional Notes
- External feed availability can vary by time/region/network policy.
- Fallback status is intentionally explicit to preserve transparency.
- Large-scale profile hydration is designed as incremental batches.

## Architecture Flowchart
## 🔁 Architecture Flow
 ```
                ┌────────────────────────────┐
                │   External APIs / Feeds    │
                │ WHO / CDC / Open Targets   │
                └────────────┬───────────────┘
                             ↓
Client → FastAPI → Services → Providers → DB + Cache → Response
                             ↓
                    Background Tasks
                   (Alert Ingestion Loop)
                             ↓
                      WebSocket Alerts
