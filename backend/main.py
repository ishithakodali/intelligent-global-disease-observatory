import asyncio
import csv
import io
import secrets
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import settings
from .db import (
    disease_profile_stats,
    get_usage_stats,
    init_db,
    list_alert_events,
    list_history,
    list_malacards_names,
    malacards_stats,
    upsert_alert_events,
    upsert_malacards_names,
)
from .profile_service import backfill_disease_profiles, build_disease_profile
from .providers.ailment_catalog import fetch_ailment_catalog
from .providers.clinical_icd import search_icd10
from .providers.malacards_import import load_malacards_names_from_file
from .providers.outbreak_feed import fetch_outbreak_feeds
from .security import InMemoryRateLimiter, resolve_client_key
from .services import build_observatory_payload, clear_cache, list_catalog

REQUIRED_SOURCES = [
    "NLM ClinicalTables ICD-10-CM",
    "WHO GHO OData",
    "Disease.sh",
    "CDC FluView",
    "ECDC Open Data",
    "Open Targets",
    "PubChem PUG-REST",
    "WHO DON RSS",
    "ProMED RSS",
    "HealthMap RSS",
]

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
)
limiter = InMemoryRateLimiter(
    max_requests=settings.rate_limit_max_requests,
    window_seconds=settings.rate_limit_window_seconds,
)
warmup_status = {
    "state": "idle",
    "last_run_utc": None,
    "message": "not started",
}
alert_ingestion_status = {
    "state": "idle",
    "last_run_utc": None,
    "last_new_alerts": 0,
    "message": "not started",
}
_websocket_clients: set[WebSocket] = set()

if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


async def _broadcast_alerts(alerts: list[dict]) -> None:
    if not alerts or not _websocket_clients:
        return
    payload = {
        "type": "new_alerts",
        "count": len(alerts),
        "alerts": alerts,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    stale: list[WebSocket] = []
    for ws in _websocket_clients:
        try:
            await ws.send_json(payload)
        except Exception:  # noqa: BLE001
            stale.append(ws)
    for ws in stale:
        _websocket_clients.discard(ws)


async def _alert_ingestion_loop() -> None:
    while True:
        try:
            feeds = await fetch_outbreak_feeds(disease="", region="")
            inserted = await upsert_alert_events(feeds.alerts)
            alert_ingestion_status["state"] = "ok"
            alert_ingestion_status["last_new_alerts"] = len(inserted)
            alert_ingestion_status["message"] = "background polling completed"
            alert_ingestion_status["last_run_utc"] = datetime.now(timezone.utc).isoformat()
            await _broadcast_alerts(inserted)
        except Exception as exc:  # noqa: BLE001
            alert_ingestion_status["state"] = "error"
            alert_ingestion_status["message"] = str(exc)
            alert_ingestion_status["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        await asyncio.sleep(max(60, settings.alert_poll_seconds))


async def _warmup_runtime_caches() -> None:
    warmup_status["state"] = "running"
    try:
        await fetch_ailment_catalog(refresh=False, per_letter_limit=300)
        await build_observatory_payload(disease="Tuberculosis", region="India", refresh=False)
        await build_observatory_payload(disease="Dengue", region="India", refresh=False)
        warmup_status["state"] = "ok"
        warmup_status["message"] = "preload caches warmed"
    except Exception as exc:  # noqa: BLE001
        warmup_status["state"] = "error"
        warmup_status["message"] = str(exc)
    finally:
        warmup_status["last_run_utc"] = datetime.now(timezone.utc).isoformat()


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    asyncio.create_task(_warmup_runtime_caches())
    asyncio.create_task(_alert_ingestion_loop())


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    client_key = resolve_client_key(request)
    limit = limiter.check(client_key)
    if not limit.allowed:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Retry in {limit.retry_after_seconds}s")

    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(limit.remaining)
    return response


@app.get("/")
def root() -> dict:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "docs": "/api/v1/docs",
    }


@app.get("/api/v1/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "warmup": warmup_status,
        "alert_ingestion": alert_ingestion_status,
    }


@app.get("/api/v1/compliance/report")
async def compliance_report() -> dict:
    infectious = await build_observatory_payload(disease="Tuberculosis", region="India", refresh=True)
    non_infectious = await build_observatory_payload(disease="Asthma", region="India", refresh=True)

    def _to_map(payload: dict) -> dict[str, dict]:
        rows = payload.get("source_status", [])
        return {
            row.get("source", "unknown"): {
                "status": row.get("status", "unknown"),
                "records": row.get("records", 0),
                "applicable": row.get("applicable", True),
                "provenance": row.get("provenance", "derived"),
            }
            for row in rows
        }

    inf_map = _to_map(infectious.model_dump(mode="json", by_alias=True))
    non_map = _to_map(non_infectious.model_dump(mode="json", by_alias=True))

    source_matrix = {}
    for source in REQUIRED_SOURCES:
        source_matrix[source] = {
            "infectious": inf_map.get(source, {"status": "missing"}),
            "non_infectious": non_map.get(source, {"status": "missing"}),
        }

    profiles = await disease_profile_stats()
    alerts = await list_alert_events(limit=1)
    latest_alert = alerts[0] if alerts else None

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "criteria": {
            "required_sources_count": len(REQUIRED_SOURCES),
            "required_sources": REQUIRED_SOURCES,
            "source_matrix": source_matrix,
        },
        "provenance_examples": {
            "infectious": infectious.provenance_summary.model_dump(mode="json"),
            "non_infectious": non_infectious.provenance_summary.model_dump(mode="json"),
        },
        "freshness": {
            "alert_ingestion": alert_ingestion_status,
            "latest_alert": latest_alert,
            "profile_store": profiles,
        },
        "realtime_interfaces": {
            "websocket_alerts_endpoint": "/api/v1/ws/alerts",
            "background_poll_seconds": max(60, settings.alert_poll_seconds),
        },
    }


@app.get("/api/v1/catalog")
def catalog() -> dict:
    return {"entries": list_catalog()}


@app.get("/api/v1/ailments/search")
async def ailment_search(q: str = Query(min_length=2), limit: int = Query(default=20, ge=1, le=100)) -> dict:
    result = await search_icd10(query=q, limit=limit)
    return {
        "source": result.source,
        "status": result.status,
        "latency_ms": result.latency_ms,
        "items": result.items,
        "message": result.message,
    }


@app.get("/api/v1/ailments/catalog")
async def ailment_catalog(refresh: bool = Query(default=False), per_letter_limit: int = Query(default=300, ge=30, le=500)) -> dict:
    result = await fetch_ailment_catalog(refresh=refresh, per_letter_limit=per_letter_limit)
    malacards_names = await list_malacards_names()
    merged_items = sorted({*result.items, *malacards_names}, key=lambda x: x.casefold())
    return {
        "source": result.source,
        "status": result.status,
        "latency_ms": result.latency_ms,
        "count": len(merged_items),
        "items": merged_items,
        "malacards_imported_count": len(malacards_names),
        "message": result.message,
    }


@app.get("/api/v1/preload")
async def preload(
    disease: str = Query(default="Tuberculosis"),
    region: str = Query(default="India"),
    include_catalog: bool = Query(default=True),
    catalog_limit: int = Query(default=25000, ge=100, le=50000),
) -> dict:
    observatory_payload = await build_observatory_payload(disease=disease, region=region, refresh=False)
    history_entries, stats_payload = await asyncio.gather(
        list_history(10),
        get_usage_stats(),
    )
    history_payload = {"entries": history_entries}

    batch_queries = "Tuberculosis:India,Dengue:India,Tuberculosis:Brazil"
    pairs = [item.strip() for item in batch_queries.split(",") if item.strip()]
    outputs: list[dict] = []
    for item in pairs:
        disease_q, region_q = [x.strip() for x in item.split(":", 1)]
        payload = await build_observatory_payload(disease=disease_q, region=region_q, refresh=False)
        outputs.append(payload.model_dump(mode="json", by_alias=True))

    catalog_payload = {"items": [], "count": 0, "malacards_imported_count": 0}
    if include_catalog:
        catalog_result = await fetch_ailment_catalog(refresh=False, per_letter_limit=300)
        malacards_names = await list_malacards_names()
        merged_items = sorted({*catalog_result.items, *malacards_names}, key=lambda x: x.casefold())
        catalog_payload = {
            "items": merged_items[:catalog_limit],
            "count": len(merged_items),
            "malacards_imported_count": len(malacards_names),
        }

    return {
        "observatory": observatory_payload.model_dump(mode="json", by_alias=True),
        "history": history_payload,
        "stats": stats_payload,
        "batch": {"count": len(outputs), "items": outputs},
        "catalog": catalog_payload,
        "warmup": warmup_status,
    }


@app.post("/api/v1/malacards/import")
async def import_malacards_file(file_path: str = Query(min_length=3), x_api_key: str = Header(default="")) -> dict:
    if settings.api_key and not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        names = await run_in_threadpool(load_malacards_names_from_file, file_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    count = await upsert_malacards_names(names, file_path)
    return {
        "imported_count": count,
        "source_file": file_path,
        "stats": await malacards_stats(),
    }


@app.get("/api/v1/malacards/stats")
async def malacards_import_stats() -> dict:
    return await malacards_stats()


@app.get("/api/v1/history")
async def history(limit: int = Query(default=25, ge=1, le=200)) -> dict:
    entries = await list_history(limit)
    return {"entries": entries}


@app.get("/api/v1/stats")
async def stats() -> dict:
    return await get_usage_stats()


@app.get("/api/v1/alerts/recent")
async def recent_alerts(
    limit: int = Query(default=50, ge=1, le=500),
    disease: str = Query(default=""),
    region: str = Query(default=""),
) -> dict:
    rows = await list_alert_events(limit=limit, disease=disease, region=region)
    return {"count": len(rows), "items": rows}


@app.get("/api/v1/disease/profile")
async def disease_profile(disease: str = Query(min_length=2), refresh: bool = Query(default=False)) -> dict:
    try:
        return await build_disease_profile(disease=disease, refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/v1/disease/profile/stats")
async def disease_profile_overview() -> dict:
    return await disease_profile_stats()


@app.post("/api/v1/disease/profile/backfill")
async def disease_profile_backfill(
    limit: int = Query(default=100, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    concurrency: int = Query(default=5, ge=1, le=20),
    refresh: bool = Query(default=False),
    x_api_key: str = Header(default=""),
) -> dict:
    if settings.api_key and not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return await backfill_disease_profiles(limit=limit, offset=offset, concurrency=concurrency, refresh=refresh)


@app.websocket("/api/v1/ws/alerts")
async def alerts_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    _websocket_clients.add(websocket)
    try:
        await websocket.send_json({
            "type": "connected",
            "message": "alerts websocket connected",
            "poll_interval_seconds": max(60, settings.alert_poll_seconds),
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _websocket_clients.discard(websocket)
    except Exception:  # noqa: BLE001
        _websocket_clients.discard(websocket)


@app.post("/api/v1/ops/cache/clear")
async def clear_runtime_cache(x_api_key: str = Header(default="")) -> dict:
    if settings.api_key and not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    removed = await clear_cache()
    return {"cleared_entries": removed}


@app.get("/api/v1/observatory")
async def observatory(
    disease: str = Query(min_length=2),
    region: str = Query(min_length=2),
    refresh: bool = Query(default=False),
) -> dict:
    try:
        payload = await build_observatory_payload(disease=disease, region=region, refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return payload.model_dump(mode="json", by_alias=True)


@app.get("/api/v1/observatory/batch")
async def observatory_batch(queries: str = Query(description="Comma-separated disease:region pairs")) -> dict:
    pairs = [item.strip() for item in queries.split(",") if item.strip()]
    if not pairs:
        raise HTTPException(status_code=400, detail="No queries provided")

    outputs: list[dict] = []
    malformed: list[str] = []
    for item in pairs[:10]:
        if ":" not in item:
            malformed.append(item)
            continue
        disease, region = [x.strip() for x in item.split(":", 1)]
        if not disease or not region:
            malformed.append(item)
            continue
        payload = await build_observatory_payload(disease=disease, region=region, refresh=False)
        outputs.append(payload.model_dump(mode="json", by_alias=True))

    return {"count": len(outputs), "items": outputs, "skipped_malformed": malformed}


@app.get("/api/v1/export/history.csv")
async def export_history_csv(limit: int = Query(default=200, ge=1, le=2000)) -> StreamingResponse:
    entries = await list_history(limit)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id",
        "disease",
        "region",
        "confidence",
        "source_ok_count",
        "source_total_count",
        "generated_at_utc",
    ])
    for row in entries:
        writer.writerow(
            [
                row["id"],
                row["disease"],
                row["region"],
                row["confidence"],
                row["source_ok_count"],
                row["source_total_count"],
                row["generated_at_utc"],
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=observatory_history.csv"},
    )
