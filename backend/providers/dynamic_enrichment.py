from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings


@dataclass
class EpidemiologySignalResult:
    source: str
    status: str
    latency_ms: int
    yearly_counts: list[dict]
    metric_label: str
    message: str = ""


@dataclass
class GeneEvidenceResult:
    source: str
    status: str
    latency_ms: int
    genes: list[dict]
    message: str = ""


@dataclass
class TherapyEvidenceResult:
    source: str
    status: str
    latency_ms: int
    therapy: dict
    message: str = ""


DISEASE_SH_HIST = "https://disease.sh/v3/covid-19/historical/all"
WHO_GHO_INDICATORS = "{base}/Indicator"
WHO_GHO_VALUES = "{base}/{indicator}"
CDC_INFLU = "https://data.cdc.gov/resource/9mfq-cb36.json"
ECDC_COVID = "https://opendata.ecdc.europa.eu/covid19/nationalcasedeath/json/"
OPEN_TARGETS_GRAPHQL = "https://api.platform.opentargets.org/api/v4/graphql"
PUBCHEM_COMPOUND_PROPERTY = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{compound}/property/Title,MolecularFormula/JSON"

THERAPY_MAP = {
    "tuberculosis": "rifampin",
    "dengue": "acetaminophen",
    "parkinson": "levodopa",
    "asthma": "salbutamol",
    "diabetes": "metformin",
    "hypertension": "amlodipine",
}

WHO_ESSENTIAL_SET = {
    "rifampin",
    "metformin",
    "amlodipine",
    "acetaminophen",
    "salbutamol",
    "levodopa",
}


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def _retry_get_json(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict | list:
    response = await client.get(url, params=params)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, (dict, list)):
        return payload
    return {}


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def _retry_post_json(client: httpx.AsyncClient, url: str, payload: dict) -> dict:
    response = await client.post(url, json=payload)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


async def fetch_disease_sh_yearly_signal(timeout_seconds: float = 8.0) -> EpidemiologySignalResult:
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            payload = await _retry_get_json(client, DISEASE_SH_HIST, params={"lastdays": "all"})

        timeline = payload.get("cases", {}) if isinstance(payload, dict) else {}
        by_year: dict[int, int] = {}
        for date_key, total in timeline.items():
            if not isinstance(date_key, str):
                continue
            try:
                year = int(date_key.split("/")[-1])
                if year < 100:
                    year += 2000
            except Exception:  # noqa: BLE001
                continue
            by_year[year] = max(by_year.get(year, 0), int(total))

        years = sorted(y for y in by_year.keys() if 2018 <= y <= 2026)
        yearly_counts: list[dict] = []
        prev = None
        for year in years:
            current = by_year[year]
            delta = current - prev if prev is not None else current
            yearly_counts.append({"year": year, "cases": max(0, delta)})
            prev = current

        latency = int((time.perf_counter() - start) * 1000)
        return EpidemiologySignalResult(
            source="Disease.sh",
            status="ok",
            latency_ms=latency,
            yearly_counts=yearly_counts,
            metric_label="Estimated Annual Cases",
            message="yearly global disease.sh trend generated",
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return EpidemiologySignalResult(
            source="Disease.sh",
            status="error",
            latency_ms=latency,
            yearly_counts=[],
            metric_label="Estimated Annual Cases",
            message=str(exc),
        )


async def fetch_who_gho_yearly_signal(region: str, timeout_seconds: float = 8.0) -> EpidemiologySignalResult:
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            indicators_payload = await _retry_get_json(
                client,
                WHO_GHO_INDICATORS.format(base=settings.who_gho_api_base),
                params={"$top": 30, "$format": "json"},
            )

            indicator_rows = indicators_payload.get("value", []) if isinstance(indicators_payload, dict) else []
            preferred = None
            for row in indicator_rows:
                name = str(row.get("IndicatorName", "")).lower()
                if "incidence" in name or "prevalence" in name:
                    preferred = row.get("IndicatorCode")
                    if preferred:
                        break

            if not preferred:
                raise ValueError("No suitable WHO GHO indicator found")

            gho = await _retry_get_json(
                client,
                WHO_GHO_VALUES.format(base=settings.who_gho_api_base, indicator=preferred),
                params={"$top": 200, "$orderby": "TimeDim desc", "$format": "json"},
            )

        values = gho.get("value", []) if isinstance(gho, dict) else []
        region_lower = region.strip().lower()
        selected = [
            row for row in values if region_lower in str(row.get("SpatialDim", "")).lower() or region_lower in str(row.get("SpatialDimType", "")).lower()
        ] or values[:50]

        yearly: dict[int, int] = {}
        for row in selected:
            year = row.get("TimeDim")
            value = row.get("NumericValue")
            if year is None or value is None:
                continue
            try:
                year_i = int(year)
                value_i = int(float(value))
            except Exception:  # noqa: BLE001
                continue
            yearly[year_i] = max(yearly.get(year_i, 0), value_i)

        yearly_counts = [{"year": y, "cases": yearly[y]} for y in sorted(yearly.keys())[-8:]]

        latency = int((time.perf_counter() - start) * 1000)
        return EpidemiologySignalResult(
            source="WHO GHO OData",
            status="ok",
            latency_ms=latency,
            yearly_counts=yearly_counts,
            metric_label="Incidence / Prevalence",
            message=f"indicator {preferred} queried",
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return EpidemiologySignalResult(
            source="WHO GHO OData",
            status="error",
            latency_ms=latency,
            yearly_counts=[],
            metric_label="Incidence / Prevalence",
            message=str(exc),
        )


async def fetch_open_targets_candidates(disease: str, timeout_seconds: float = 7.0) -> GeneEvidenceResult:
    start = time.perf_counter()
    try:
        search_query = {
            "query": """
            query SearchDisease($term: String!) {
              search(queryString: $term, entityNames: ["disease"], page: {index: 0, size: 1}) {
                hits { id name }
              }
            }
            """,
            "variables": {"term": disease},
        }

        target_query = {
            "query": """
            query DiseaseTargets($id: String!) {
              disease(efoId: $id) {
                associatedTargets(page: {index: 0, size: 8}) {
                  rows {
                    score
                    target {
                      approvedSymbol
                      approvedName
                    }
                  }
                }
              }
            }
            """,
            "variables": {},
        }

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            s_payload = await _retry_post_json(client, OPEN_TARGETS_GRAPHQL, search_query)
            hits = s_payload.get("data", {}).get("search", {}).get("hits", [])
            if not hits:
                raise ValueError("No Open Targets disease hit found")
            disease_id = hits[0].get("id")
            target_query["variables"] = {"id": disease_id}
            t_payload = await _retry_post_json(client, OPEN_TARGETS_GRAPHQL, target_query)

        rows = t_payload.get("data", {}).get("disease", {}).get("associatedTargets", {}).get("rows", [])
        genes = []
        for row in rows:
            target = row.get("target", {})
            symbol = target.get("approvedSymbol")
            if not symbol:
                continue
            score = float(row.get("score") or 0.0)
            genes.append(
                {
                    "symbol": symbol,
                    "score": round(min(0.99, max(0.1, score)), 2),
                    "summary": str(target.get("approvedName") or "Open Targets association"),
                }
            )

        latency = int((time.perf_counter() - start) * 1000)
        return GeneEvidenceResult(
            source="Open Targets",
            status="ok",
            latency_ms=latency,
            genes=genes[:6],
            message="target associations returned",
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return GeneEvidenceResult(
            source="Open Targets",
            status="error",
            latency_ms=latency,
            genes=[],
            message=str(exc),
        )


async def fetch_pubchem_therapy_hint(disease: str, timeout_seconds: float = 7.0) -> TherapyEvidenceResult:
    start = time.perf_counter()
    default_therapy = {
        "drug": "Not yet curated",
        "mechanism": "No validated PubChem compound mapping found for this ailment.",
        "who_essential": "Unknown",
        "guideline": "Fallback response. Add explicit disease-to-treatment mapping for stricter clinical accuracy.",
    }

    try:
        disease_key = disease.strip().lower()
        mapped = None
        for key, compound in THERAPY_MAP.items():
            if key in disease_key:
                mapped = compound
                break

        if not mapped:
            mapped = disease.strip().split(" ")[0]

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            payload = await _retry_get_json(
                client,
                PUBCHEM_COMPOUND_PROPERTY.format(compound=quote(mapped)),
            )

        props = payload.get("PropertyTable", {}).get("Properties", [])
        if not props:
            latency = int((time.perf_counter() - start) * 1000)
            return TherapyEvidenceResult(
                source="PubChem PUG-REST",
                status="fallback",
                latency_ms=latency,
                therapy=default_therapy,
                message="compound not found in PubChem",
            )

        prop = props[0]
        drug = str(prop.get("Title") or mapped)
        formula = str(prop.get("MolecularFormula") or "")

        therapy = {
            "drug": drug,
            "mechanism": f"Mapped via PubChem compound registry ({formula}).",
            "who_essential": "Yes" if mapped.lower() in WHO_ESSENTIAL_SET else "Unknown",
            "guideline": "Therapy hint derived from disease-to-compound mapping plus PubChem validation.",
        }

        latency = int((time.perf_counter() - start) * 1000)
        return TherapyEvidenceResult(
            source="PubChem PUG-REST",
            status="ok",
            latency_ms=latency,
            therapy=therapy,
            message="therapy hint extracted",
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return TherapyEvidenceResult(
            source="PubChem PUG-REST",
            status="error",
            latency_ms=latency,
            therapy=default_therapy,
            message=str(exc),
        )


async def fetch_live_epidemiology_signal(disease: str, region: str) -> EpidemiologySignalResult:
    who_result, disease_sh_result = await asyncio.gather(
        fetch_who_gho_yearly_signal(region=region),
        fetch_disease_sh_yearly_signal(),
    )

    if who_result.status == "ok" and len(who_result.yearly_counts) >= 3:
        return who_result
    if disease_sh_result.status == "ok" and len(disease_sh_result.yearly_counts) >= 3:
        return disease_sh_result

    fallback_message = "; ".join(
        [msg for msg in [who_result.message, disease_sh_result.message] if msg]
    )
    return EpidemiologySignalResult(
        source="WHO GHO OData + Disease.sh",
        status="error",
        latency_ms=max(who_result.latency_ms, disease_sh_result.latency_ms),
        yearly_counts=[],
        metric_label="Cases",
        message=fallback_message or "no epidemiology signal available",
    )


async def fetch_primary_epidemiology_pair(region: str) -> tuple[EpidemiologySignalResult, EpidemiologySignalResult]:
    return await asyncio.gather(
        fetch_who_gho_yearly_signal(region=region),
        fetch_disease_sh_yearly_signal(),
    )


async def fetch_cdc_yearly_signal(timeout_seconds: float = 8.0) -> EpidemiologySignalResult:
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            rows = await _retry_get_json(
                client,
                CDC_INFLU,
                params={"$limit": 200, "$order": "year desc", "$where": "year >= 2018"},
            )

        # Some CDC endpoints may return list payloads; normalize gracefully.
        records = rows if isinstance(rows, list) else rows.get("results", []) if isinstance(rows, dict) else []
        yearly: dict[int, int] = {}
        for row in records:
            try:
                year = int(row.get("year"))
                val = int(float(row.get("weekly_rate") or row.get("incidence_rate") or 0))
            except Exception:  # noqa: BLE001
                continue
            yearly[year] = max(yearly.get(year, 0), val)

        yearly_counts = [{"year": y, "cases": yearly[y]} for y in sorted(yearly.keys())[-8:]]
        latency = int((time.perf_counter() - start) * 1000)
        return EpidemiologySignalResult(
            source="CDC FluView",
            status="ok" if yearly_counts else "fallback",
            latency_ms=latency,
            yearly_counts=yearly_counts,
            metric_label="Reported Rate",
            message="cdc influenza-style trend loaded" if yearly_counts else "no parseable CDC rows",
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return EpidemiologySignalResult(
            source="CDC FluView",
            status="error",
            latency_ms=latency,
            yearly_counts=[],
            metric_label="Reported Rate",
            message=str(exc),
        )


async def fetch_ecdc_yearly_signal(timeout_seconds: float = 8.0) -> EpidemiologySignalResult:
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            body = await _retry_get_json(client, ECDC_COVID)

        records = body.get("records", []) if isinstance(body, dict) else []
        yearly: dict[int, int] = {}
        for row in records:
            date = str(row.get("dateRep") or "")
            if len(date) < 4:
                continue
            try:
                year = int(date[-4:])
                cases = int(row.get("cases") or 0)
            except Exception:  # noqa: BLE001
                continue
            yearly[year] = yearly.get(year, 0) + max(0, cases)

        yearly_counts = [{"year": y, "cases": yearly[y]} for y in sorted(yearly.keys())[-8:]]
        latency = int((time.perf_counter() - start) * 1000)
        return EpidemiologySignalResult(
            source="ECDC Open Data",
            status="ok" if yearly_counts else "fallback",
            latency_ms=latency,
            yearly_counts=yearly_counts,
            metric_label="Annual Cases",
            message="ecdc aggregate trend loaded" if yearly_counts else "no parseable ECDC rows",
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return EpidemiologySignalResult(
            source="ECDC Open Data",
            status="error",
            latency_ms=latency,
            yearly_counts=[],
            metric_label="Annual Cases",
            message=str(exc),
        )
