# Intelligent Global Disease Observatory API

Backend-only FastAPI service for disease observability, enrichment, catalog search, and analytics.

## Core Features

- Typed FastAPI API with OpenAPI docs
- Dynamic disease resolution via ICD-10-CM search
- Multi-source enrichment (WHO GHO, Disease.sh, Open Targets, PubChem PUG-REST)
- High-volume ailment catalog endpoint with MalaCards import support
- Query history and usage stats persisted in SQLite
- In-memory rate limiting and hybrid cache (Redis + local TTL fallback)
- Background outbreak ingestion loop (WHO DON, ProMED, HealthMap)
- WebSocket push for live outbreak alerts

## API Docs

- Swagger UI: http://127.0.0.1:8000/api/v1/docs
- ReDoc: http://127.0.0.1:8000/api/v1/redoc
- OpenAPI JSON: http://127.0.0.1:8000/api/v1/openapi.json

## Key Endpoints

- GET /api/v1/health
- GET /api/v1/catalog
- GET /api/v1/ailments/search?q=diabetes&limit=25
- GET /api/v1/ailments/catalog?refresh=false&per_letter_limit=500
- POST /api/v1/malacards/import?file_path=C:/path/to/export.csv
- GET /api/v1/malacards/stats
- GET /api/v1/observatory?disease=Tuberculosis&region=India&refresh=true
- GET /api/v1/observatory/batch?queries=Tuberculosis:India,Dengue:India
- GET /api/v1/history?limit=25
- GET /api/v1/stats
- GET /api/v1/alerts/recent?limit=50&disease=tb&region=india
- GET /api/v1/disease/profile?disease=asthma&refresh=false
- GET /api/v1/disease/profile/stats
- POST /api/v1/disease/profile/backfill?limit=500&offset=0&concurrency=8
- GET /api/v1/compliance/report
- WS /api/v1/ws/alerts
- GET /api/v1/export/history.csv?limit=500
- POST /api/v1/ops/cache/clear

## Local Run

1. Install dependencies

```powershell
./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

2. Start API

```powershell
./.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

3. Verify

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health"
```

## GitHub Readiness

- Repository-level ignore rules are in `.gitignore` to exclude local DBs, generated exports, virtual environments, and secrets.
- Copy `.env.example` to `.env` and set environment values for your deployment target.
- Do not commit `.env` or local runtime artifacts.

## Configuration

Settings are environment-driven:

- APP_NAME
- APP_VERSION
- CACHE_TTL_SECONDS
- OUTBREAK_FEED_URL
- API_KEY
- RATE_LIMIT_WINDOW_SECONDS
- RATE_LIMIT_MAX_REQUESTS
- ALLOWED_ORIGINS (comma-separated)
- NCBI_API_KEY
- REDIS_URL
- ALERT_POLL_SECONDS
- PROMED_FEED_URL
- HEALTHMAP_FEED_URL
- WHO_GHO_API_BASE

## Docker

Run backend-only compose stack:

```powershell
docker compose up --build
```

Compose includes a Redis service and automatically injects REDIS_URL into the API container.

## Profile Enrichment Sources

The disease profile endpoints aggregate and persist data from multiple open sources:

- ICD lookup via NLM ClinicalTables
- Epidemiology signal via WHO GHO OData, Disease.sh, CDC, and ECDC feeds
- Gene associations via Open Targets
- Therapeutic hints via PubChem PUG-REST
- Summary and aliases via Wikipedia and Wikidata
- Research snapshot via ClinicalTrials.gov, MedlinePlus, and OpenAlex

Use backfill endpoint in batches to hydrate MalaCards disease names with profile content.

Automated bulk hydration (PowerShell):

```powershell
./scripts/backfill_profiles.ps1 -BatchSize 100 -Concurrency 8 -MaxRounds 50
```

Set `-MaxRounds 0` to run until fully hydrated.
