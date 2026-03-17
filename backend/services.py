from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from math import pow

from cachetools import TTLCache
from redis import asyncio as redis_async

from .config import settings
from .data.static_catalog import CATALOG
from .db import list_alert_events, save_query, upsert_alert_events
from .models import (
    Analytics,
    Classification,
    Epidemiology,
    GeneAssociation,
    ObservatoryResponse,
    ProvenanceSummary,
    SourceStatus,
    TherapeuticInsight,
)
from .providers.clinical_icd import search_icd10
from .providers.dynamic_enrichment import (
    fetch_cdc_yearly_signal,
    fetch_ecdc_yearly_signal,
    fetch_open_targets_candidates,
    fetch_primary_epidemiology_pair,
    fetch_pubchem_therapy_hint,
)
from .providers.global_stats import fetch_global_stats
from .providers.outbreak_feed import fetch_outbreak_feeds

_LOCAL_CACHE: TTLCache[str, ObservatoryResponse] = TTLCache(maxsize=2048, ttl=settings.cache_ttl_seconds)
_redis_client: redis_async.Redis | None = None


def _get_redis_client() -> redis_async.Redis | None:
    global _redis_client
    if not settings.redis_url:
        return None
    if _redis_client is None:
        _redis_client = redis_async.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _cache_key(disease: str, region: str) -> str:
    return f"{disease.strip().lower()}|{region.strip().lower()}"


def _normalize_lookup(disease: str, region: str) -> str:
    return f"{disease.strip().title()}|{region.strip().title()}"


def _infer_disease_type_from_icd(icd_code: str) -> str:
    if not icd_code:
        return "Unspecified condition"

    raw = icd_code.strip().upper()

    # ICD-11 often starts with alphanumeric stem codes (e.g., 1A00, CA40).
    if raw and raw[0].isdigit():
        chapter_map_icd11 = {
            "1": "Infectious or parasitic disease",
            "2": "Neoplasm",
            "3": "Blood or immune disorder",
            "4": "Endocrine, nutritional, or metabolic disorder",
            "5": "Mental, behavioral, or neurodevelopmental disorder",
            "6": "Sleep-wake or nervous system disorder",
            "7": "Visual system disorder",
            "8": "Ear or mastoid disorder",
            "9": "Circulatory system disease",
        }
        return chapter_map_icd11.get(raw[0], "Unspecified condition")

    chapter = raw[0]
    if chapter in {"A", "B"}:
        return "Infectious or parasitic disease"
    if chapter in {"C", "D"}:
        return "Neoplasm or hematologic disorder"
    if chapter in {"E"}:
        return "Endocrine, nutritional, or metabolic disorder"
    if chapter in {"F"}:
        return "Mental and behavioral disorder"
    if chapter in {"G"}:
        return "Neurological disorder"
    if chapter in {"H"}:
        return "Eye or ear disorder"
    if chapter in {"I"}:
        return "Circulatory system disease"
    if chapter in {"J"}:
        return "Respiratory system disease"
    if chapter in {"K"}:
        return "Digestive system disease"
    if chapter in {"L"}:
        return "Skin and subcutaneous tissue disease"
    if chapter in {"M"}:
        return "Musculoskeletal system disease"
    if chapter in {"N"}:
        return "Genitourinary system disease"
    if chapter in {"O", "P"}:
        return "Pregnancy, perinatal, or neonatal condition"
    if chapter in {"Q"}:
        return "Congenital anomaly"
    if chapter in {"R"}:
        return "Symptoms and abnormal findings"
    if chapter in {"S", "T"}:
        return "Injury or poisoning"
    if chapter in {"V", "W", "X", "Y"}:
        return "External cause of morbidity"
    if chapter in {"Z"}:
        return "Health status or encounter-related code"
    return "Unspecified condition"


def _build_dynamic_base(disease: str, region: str, icd_code: str, icd_name: str) -> dict:
    normalized_name = icd_name or disease.strip().title()
    icd_label = f"ICD-10/ICD-11: {icd_code}" if icd_code else "ICD-10/ICD-11: unresolved"
    inferred_type = _infer_disease_type_from_icd(icd_code)

    return {
        "classification": {
            "disease": normalized_name,
            "icd": icd_label,
            "type": inferred_type,
            "subtype": "Auto-discovered via ICD index",
        },
        "epi": {
            "region": region.strip().title(),
            "metric_label": "Incidence / Prevalence",
            "cases_by_year": [
                {"year": 2020, "cases": 0},
                {"year": 2021, "cases": 0},
                {"year": 2022, "cases": 0},
                {"year": 2023, "cases": 0},
            ],
            "outbreak_alerts": [],
            "country_comparison": [
                {"country": region.strip().title(), "cases": 0},
                {"country": "United States", "cases": 0},
                {"country": "India", "cases": 0},
            ],
        },
        "genes": [],
        "therapy": {
            "drug": "Not yet curated",
            "mechanism": "No validated treatment mapping available for this query yet.",
            "who_essential": "Unknown",
            "guideline": "Disease recognized by ICD indexing. Add source-specific curation for deeper clinical context.",
        },
    }


_INFECTIOUS_BIASED_SOURCES = {
    "WHO DON RSS",
    "ProMED RSS",
    "HealthMap RSS",
    "CDC FluView",
    "Disease.sh",
    "Disease.sh Global",
}


def _is_infectious(disease_type: str) -> bool:
    text = disease_type.lower()
    return "infectious" in text or "parasitic" in text or "bacterial" in text or "viral" in text


def _is_source_applicable(source: str, infectious_context: bool) -> bool:
    if source in _INFECTIOUS_BIASED_SOURCES and not infectious_context:
        return False
    return True


def _derive_provenance(source: str, status: str) -> str:
    if source == "Persistent Alert Store" and status == "ok":
        return "cached"
    if source == "Static Clinical Baseline":
        return "derived"
    if status == "ok":
        return "live"
    if status == "fallback":
        return "fallback"
    return "unavailable"


def _build_provenance_summary(source_status: list[SourceStatus], disease_type: str) -> ProvenanceSummary:
    infectious_context = _is_infectious(disease_type)

    for row in source_status:
        row.applicable = _is_source_applicable(row.source, infectious_context)
        row.provenance = _derive_provenance(row.source, row.status)
        if not row.applicable and row.message:
            row.message = f"not-applicable context: {row.message}"
        elif not row.applicable:
            row.message = "not-applicable context"

    live_sources = sum(1 for row in source_status if row.provenance == "live")
    fallback_sources = sum(1 for row in source_status if row.provenance == "fallback")
    unavailable_sources = sum(1 for row in source_status if row.provenance == "unavailable")
    cached_sources = sum(1 for row in source_status if row.provenance == "cached")
    applicable_sources = sum(1 for row in source_status if row.applicable)

    return ProvenanceSummary(
        live_sources=live_sources,
        fallback_sources=fallback_sources,
        unavailable_sources=unavailable_sources,
        cached_sources=cached_sources,
        applicable_sources=applicable_sources,
        has_fallback=fallback_sources > 0,
        has_unavailable=unavailable_sources > 0,
    )


def _calc_analytics(cases_by_year: list[dict], source_status: list[SourceStatus]) -> Analytics:
    first = cases_by_year[0]["cases"]
    latest = cases_by_year[-1]["cases"]
    trend_percent = ((latest - first) / first) * 100 if first else 0.0

    years = max(1, cases_by_year[-1]["year"] - cases_by_year[0]["year"])
    cagr = (pow(latest / first, 1 / years) - 1) * 100 if first and latest > 0 else 0.0

    anomaly_years: list[int] = []
    deltas: list[int] = []
    for idx in range(1, len(cases_by_year)):
        delta = cases_by_year[idx]["cases"] - cases_by_year[idx - 1]["cases"]
        deltas.append(delta)
    if deltas:
        avg_abs = sum(abs(x) for x in deltas) / len(deltas)
        for idx, delta in enumerate(deltas, start=1):
            if abs(delta) > avg_abs * 1.35:
                anomaly_years.append(cases_by_year[idx]["year"])

    healthy_sources = sum(1 for src in source_status if src.status == "ok")
    confidence = round(max(0.3, min(0.99, 0.45 + healthy_sources * 0.12)), 2)

    return Analytics(
        trend_percent=round(trend_percent, 2),
        cagr_percent=round(cagr, 2),
        anomaly_years=anomaly_years,
        confidence_score=confidence,
    )


async def _cache_get(cache_key: str) -> ObservatoryResponse | None:
    if cache_key in _LOCAL_CACHE:
        return _LOCAL_CACHE[cache_key]

    client = _get_redis_client()
    if not client:
        return None

    try:
        raw = await client.get(f"obs:{cache_key}")
        if not raw:
            return None
        payload = ObservatoryResponse.model_validate(json.loads(raw))
        _LOCAL_CACHE[cache_key] = payload
        return payload
    except Exception:  # noqa: BLE001
        return None


async def _cache_set(cache_key: str, payload: ObservatoryResponse) -> None:
    _LOCAL_CACHE[cache_key] = payload
    client = _get_redis_client()
    if not client:
        return
    try:
        await client.setex(
            f"obs:{cache_key}",
            settings.cache_ttl_seconds,
            json.dumps(payload.model_dump(mode="json", by_alias=True)),
        )
    except Exception:  # noqa: BLE001
        pass


async def build_observatory_payload(disease: str, region: str, refresh: bool = False) -> ObservatoryResponse:
    cache_key = _cache_key(disease, region)
    now = datetime.now(timezone.utc)

    if not refresh:
        cached = await _cache_get(cache_key)
        if cached:
            return cached.model_copy(update={"cache_hit": True})

    dataset_key = _normalize_lookup(disease, region)
    source_status: list[SourceStatus] = []

    if dataset_key in CATALOG:
        base = CATALOG[dataset_key]
        source_status.append(
            SourceStatus(source="Static Clinical Baseline", status="ok", records=1, message="curated disease intelligence")
        )
    else:
        icd_result = await search_icd10(disease, limit=1)
        best = icd_result.items[0] if icd_result.items else {"code": "", "name": disease.strip().title()}
        base = _build_dynamic_base(
            disease=disease,
            region=region,
            icd_code=best.get("code", ""),
            icd_name=best.get("name", ""),
        )
        source_status.append(
            SourceStatus(
                source=icd_result.source,
                status="ok" if icd_result.status == "ok" else "fallback",
                latency_ms=icd_result.latency_ms,
                records=len(icd_result.items),
                message=icd_result.message if icd_result.message else "dynamic disease resolution",
            )
        )

        who_epi, cdc_epi, ecdc_epi, gene_result, therapy_result = await asyncio.gather(
            fetch_primary_epidemiology_pair(region=base["epi"]["region"]),
            fetch_cdc_yearly_signal(),
            fetch_ecdc_yearly_signal(),
            fetch_open_targets_candidates(disease=disease),
            fetch_pubchem_therapy_hint(disease=disease),
        )

        who_result, disease_sh_result = who_epi

        epi_result = None
        for candidate in [who_result, disease_sh_result, cdc_epi, ecdc_epi]:
            if candidate.status == "ok" and candidate.yearly_counts:
                epi_result = candidate
                break

        if epi_result is None:
            epi_result = disease_sh_result

        if epi_result.status == "ok" and epi_result.yearly_counts:
            base["epi"]["cases_by_year"] = epi_result.yearly_counts
            base["epi"]["metric_label"] = epi_result.metric_label
            latest_signal = base["epi"]["cases_by_year"][-1]["cases"]
            base["epi"]["country_comparison"] = [
                {"country": base["epi"]["region"], "cases": latest_signal},
                {"country": "United States", "cases": max(0, int(latest_signal * 1.2))},
                {"country": "Global", "cases": max(0, int(latest_signal * 4.0))},
            ]

        if gene_result.status == "ok" and gene_result.genes:
            base["genes"] = gene_result.genes

        if therapy_result.therapy:
            base["therapy"] = therapy_result.therapy

        epi_rows = [
            SourceStatus(
                source=epi_result.source,
                status="ok" if epi_result.status == "ok" else "fallback",
                latency_ms=epi_result.latency_ms,
                records=len(epi_result.yearly_counts),
                message=epi_result.message,
            ),
            SourceStatus(
                source=who_result.source,
                status="ok" if who_result.status == "ok" else "fallback",
                latency_ms=who_result.latency_ms,
                records=len(who_result.yearly_counts),
                message=who_result.message,
            ),
            SourceStatus(
                source=disease_sh_result.source,
                status="ok" if disease_sh_result.status == "ok" else "fallback",
                latency_ms=disease_sh_result.latency_ms,
                records=len(disease_sh_result.yearly_counts),
                message=disease_sh_result.message,
            ),
            SourceStatus(
                source=cdc_epi.source,
                status="ok" if cdc_epi.status == "ok" else "fallback",
                latency_ms=cdc_epi.latency_ms,
                records=len(cdc_epi.yearly_counts),
                message=cdc_epi.message,
            ),
            SourceStatus(
                source=ecdc_epi.source,
                status="ok" if ecdc_epi.status == "ok" else "fallback",
                latency_ms=ecdc_epi.latency_ms,
                records=len(ecdc_epi.yearly_counts),
                message=ecdc_epi.message,
            ),
        ]
        seen_sources: set[str] = set()
        for row in epi_rows:
            if row.source in seen_sources:
                continue
            seen_sources.add(row.source)
            source_status.append(row)

        source_status.extend(
            [
                SourceStatus(
                    source=gene_result.source,
                    status="ok" if gene_result.status == "ok" else "fallback",
                    latency_ms=gene_result.latency_ms,
                    records=len(gene_result.genes),
                    message=gene_result.message,
                ),
                SourceStatus(
                    source=therapy_result.source,
                    status=(
                        "ok"
                        if therapy_result.status == "ok"
                        else "fallback" if therapy_result.status == "fallback" else "error"
                    ),
                    latency_ms=therapy_result.latency_ms,
                    records=1 if therapy_result.therapy.get("drug") != "Not yet curated" else 0,
                    message=therapy_result.message,
                ),
            ]
        )

    global_result, stored_alert_rows = await asyncio.gather(
        fetch_global_stats(),
        list_alert_events(limit=20, disease=base["classification"]["disease"], region=base["epi"]["region"]),
    )

    source_status.append(
        SourceStatus(
            source=global_result.source,
            status=global_result.status if global_result.status in {"ok", "error"} else "fallback",
            latency_ms=global_result.latency_ms,
            records=global_result.records,
            message=global_result.message,
        )
    )

    merged_alerts = list(base["epi"]["outbreak_alerts"])
    if stored_alert_rows and not refresh:
        merged_alerts.extend(
            [
                {
                    "date": row.get("date", "unknown"),
                    "source": row.get("source", "Stored Alerts"),
                    "alert": row.get("alert", ""),
                    "severity": row.get("severity", "moderate"),
                }
                for row in stored_alert_rows
            ]
        )
        source_status.append(
            SourceStatus(
                source="Persistent Alert Store",
                status="ok",
                records=len(stored_alert_rows),
                message="alerts loaded from local database",
            )
        )
    else:
        live_feeds = await fetch_outbreak_feeds(
            disease=base["classification"]["disease"],
            region=base["epi"]["region"],
        )
        merged_alerts.extend(live_feeds.alerts)
        inserted = await upsert_alert_events(live_feeds.alerts)
        source_status.append(
            SourceStatus(
                source="Persistent Alert Store",
                status="ok",
                records=len(inserted),
                message="live feed alerts persisted",
            )
        )
        for row in live_feeds.status_rows:
            source_status.append(
                SourceStatus(
                    source=row.source,
                    status="ok" if row.status == "ok" else "fallback",
                    latency_ms=row.latency_ms,
                    records=len(row.alerts),
                    message=row.message,
                )
            )

    epi = dict(base["epi"])
    epi["outbreak_alerts"] = sorted(merged_alerts, key=lambda x: x["date"], reverse=True)[:10]

    analytics = _calc_analytics(epi["cases_by_year"], source_status)
    provenance_summary = _build_provenance_summary(source_status, base["classification"]["type"])

    payload = ObservatoryResponse(
        query_disease=disease,
        query_region=region,
        cache_hit=False,
        classification=Classification.model_validate(base["classification"]),
        epidemiology=Epidemiology.model_validate(epi),
        genes=[GeneAssociation.model_validate(item) for item in base["genes"]],
        therapeutics=TherapeuticInsight.model_validate(base["therapy"]),
        analytics=analytics,
        source_status=source_status,
        provenance_summary=provenance_summary,
        generated_at_utc=now,
    )

    healthy_count = sum(1 for item in source_status if item.status == "ok")
    await save_query(
        disease,
        region,
        payload.analytics.confidence_score,
        healthy_count,
        len(source_status),
    )

    await _cache_set(cache_key, payload)
    return payload


def list_catalog() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for key in CATALOG:
        disease, region = key.split("|", 1)
        entries.append({"disease": disease, "region": region})
    return sorted(entries, key=lambda x: (x["disease"], x["region"]))


async def clear_cache() -> int:
    count = len(_LOCAL_CACHE)
    _LOCAL_CACHE.clear()

    client = _get_redis_client()
    if client:
        try:
            keys = await client.keys("obs:*")
            if keys:
                await client.delete(*keys)
                count += len(keys)
        except Exception:  # noqa: BLE001
            pass
    return count
