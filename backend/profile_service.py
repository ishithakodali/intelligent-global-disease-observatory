from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .db import (
    get_disease_profile,
    list_malacards_without_profile,
    upsert_disease_profile,
)
from .providers.clinical_icd import search_icd10
from .providers.disease_profile_sources import (
    fetch_clinicaltrials_snapshot,
    fetch_medlineplus_snapshot,
    fetch_openalex_snapshot,
    fetch_wikidata_facts,
    fetch_wikipedia_summary,
)
from .providers.dynamic_enrichment import (
    fetch_live_epidemiology_signal,
    fetch_open_targets_candidates,
    fetch_pubchem_therapy_hint,
)


async def build_disease_profile(disease: str, refresh: bool = False) -> dict:
    name = disease.strip()
    if not name:
        raise ValueError("Disease name is required")

    if not refresh:
        existing = await get_disease_profile(name)
        if existing:
            return {
                "disease": existing["disease"],
                "profile": existing["profile"],
                "source_ok_count": existing["source_ok_count"],
                "updated_at_utc": existing["updated_at_utc"],
                "cache_hit": True,
            }

    icd_task = search_icd10(name, limit=1)
    epi_task = fetch_live_epidemiology_signal(disease=name, region="Global")
    gene_task = fetch_open_targets_candidates(disease=name)
    therapy_task = fetch_pubchem_therapy_hint(disease=name)

    wiki_task = fetch_wikipedia_summary(name)
    wikidata_task = fetch_wikidata_facts(name)
    trial_task = fetch_clinicaltrials_snapshot(name)
    medline_task = fetch_medlineplus_snapshot(name)
    openalex_task = fetch_openalex_snapshot(name)

    icd_result, epi_result, gene_result, therapy_result, wiki, wikidata, trial, medline, openalex = await asyncio.gather(
        icd_task,
        epi_task,
        gene_task,
        therapy_task,
        wiki_task,
        wikidata_task,
        trial_task,
        medline_task,
        openalex_task,
    )

    icd_best = icd_result.items[0] if icd_result.items else {"code": "", "name": name}

    sources = [
        {
            "source": icd_result.source,
            "status": icd_result.status,
            "latency_ms": icd_result.latency_ms,
            "records": len(icd_result.items),
            "message": icd_result.message,
        },
        {
            "source": epi_result.source,
            "status": epi_result.status,
            "latency_ms": epi_result.latency_ms,
            "records": len(epi_result.yearly_counts),
            "message": epi_result.message,
        },
        {
            "source": gene_result.source,
            "status": gene_result.status,
            "latency_ms": gene_result.latency_ms,
            "records": len(gene_result.genes),
            "message": gene_result.message,
        },
        {
            "source": therapy_result.source,
            "status": therapy_result.status,
            "latency_ms": therapy_result.latency_ms,
            "records": 1 if therapy_result.therapy else 0,
            "message": therapy_result.message,
        },
        {
            "source": wiki.source,
            "status": wiki.status,
            "latency_ms": wiki.latency_ms,
            "records": 1 if wiki.payload else 0,
            "message": wiki.message,
        },
        {
            "source": wikidata.source,
            "status": wikidata.status,
            "latency_ms": wikidata.latency_ms,
            "records": 1 if wikidata.payload else 0,
            "message": wikidata.message,
        },
        {
            "source": trial.source,
            "status": trial.status,
            "latency_ms": trial.latency_ms,
            "records": int(trial.payload.get("sampled_studies", 0)) if trial.payload else 0,
            "message": trial.message,
        },
        {
            "source": medline.source,
            "status": medline.status,
            "latency_ms": medline.latency_ms,
            "records": int(medline.payload.get("result_count", 0)) if medline.payload else 0,
            "message": medline.message,
        },
        {
            "source": openalex.source,
            "status": openalex.status,
            "latency_ms": openalex.latency_ms,
            "records": int(openalex.payload.get("works_count", 0)) if openalex.payload else 0,
            "message": openalex.message,
        },
    ]

    profile = {
        "disease": name,
        "resolved_name": icd_best.get("name") or name,
        "icd": {
            "code": icd_best.get("code", ""),
            "source": icd_result.source,
        },
        "summary": {
            "short": wiki.payload.get("summary", "") if wiki.payload else "",
            "description": wikidata.payload.get("description", "") if wikidata.payload else "",
        },
        "synonyms": wikidata.payload.get("aliases", []) if wikidata.payload else [],
        "epidemiology": {
            "metric_label": epi_result.metric_label,
            "yearly": epi_result.yearly_counts,
            "source": epi_result.source,
        },
        "genomics": {
            "genes": gene_result.genes,
            "source": gene_result.source,
        },
        "therapeutics": {
            "therapy": therapy_result.therapy,
            "source": therapy_result.source,
        },
        "research": {
            "clinical_trials": trial.payload,
            "openalex": openalex.payload,
            "medlineplus": medline.payload,
        },
        "knowledge_links": {
            "wikipedia": wiki.payload.get("url", "") if wiki.payload else "",
            "wikidata": wikidata.payload.get("url", "") if wikidata.payload else "",
            "medlineplus": medline.payload.get("top_url", "") if medline.payload else "",
        },
        "source_diagnostics": sources,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    source_ok_count = sum(1 for row in sources if row["status"] == "ok")
    stored = await upsert_disease_profile(name, profile, source_ok_count)
    return {
        "disease": stored["disease"],
        "profile": profile,
        "source_ok_count": source_ok_count,
        "updated_at_utc": stored["updated_at_utc"],
        "cache_hit": False,
    }


async def backfill_disease_profiles(limit: int = 100, offset: int = 0, concurrency: int = 5, refresh: bool = False) -> dict:
    names = await list_malacards_without_profile(limit=limit, offset=offset)
    if not names:
        return {
            "requested_limit": limit,
            "offset": offset,
            "queued": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "errors": [],
        }

    sem = asyncio.Semaphore(max(1, min(20, concurrency)))
    errors: list[dict] = []

    async def _worker(name: str) -> bool:
        async with sem:
            try:
                await build_disease_profile(name, refresh=refresh)
                return True
            except Exception as exc:  # noqa: BLE001
                errors.append({"disease": name, "error": str(exc)})
                return False

    results = await asyncio.gather(*[_worker(name) for name in names])
    success = sum(1 for item in results if item)
    return {
        "requested_limit": limit,
        "offset": offset,
        "queued": len(names),
        "processed": len(results),
        "success": success,
        "failed": len(results) - success,
        "errors": errors[:25],
    }
