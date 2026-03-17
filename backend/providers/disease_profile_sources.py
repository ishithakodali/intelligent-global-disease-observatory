from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


@dataclass
class SourcePayload:
    source: str
    status: str
    latency_ms: int
    payload: dict
    message: str = ""


WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKIDATA_SEARCH = "https://www.wikidata.org/w/api.php"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{entity}.json"
CLINICALTRIALS_API = "https://clinicaltrials.gov/api/v2/studies"
MEDLINEPLUS_SEARCH = "https://wsearch.nlm.nih.gov/ws/query"
OPENALEX_WORKS = "https://api.openalex.org/works"


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def _get_json(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict:
    response = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def _get_text(client: httpx.AsyncClient, url: str, params: dict | None = None) -> str:
    response = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    return response.text


async def fetch_wikipedia_summary(disease: str, timeout_seconds: float = 8.0) -> SourcePayload:
    start = time.perf_counter()
    try:
        title = quote(disease.strip().replace(" ", "_"))
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            body = await _get_json(client, WIKIPEDIA_SUMMARY.format(title=title))

        payload = {
            "title": body.get("title", disease),
            "description": body.get("description", ""),
            "summary": body.get("extract", ""),
            "url": body.get("content_urls", {}).get("desktop", {}).get("page", ""),
            "thumbnail": body.get("thumbnail", {}).get("source", ""),
        }
        latency = int((time.perf_counter() - start) * 1000)
        return SourcePayload("Wikipedia", "ok", latency, payload, "summary loaded")
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return SourcePayload("Wikipedia", "error", latency, {}, str(exc))


async def fetch_wikidata_facts(disease: str, timeout_seconds: float = 8.0) -> SourcePayload:
    start = time.perf_counter()
    try:
        params = {
            "action": "wbsearchentities",
            "search": disease,
            "language": "en",
            "format": "json",
            "limit": 1,
            "type": "item",
        }
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            search = await _get_json(client, WIKIDATA_SEARCH, params=params)
            hits = search.get("search", [])
            if not hits:
                raise ValueError("No Wikidata entity found")
            entity_id = hits[0].get("id")
            if not entity_id:
                raise ValueError("Missing entity id")
            entity = await _get_json(client, WIKIDATA_ENTITY.format(entity=entity_id))

        entity_payload = entity.get("entities", {}).get(entity_id, {})
        aliases = entity_payload.get("aliases", {}).get("en", [])
        alias_values = [item.get("value", "") for item in aliases if item.get("value")][:10]

        payload = {
            "wikidata_id": entity_id,
            "label": entity_payload.get("labels", {}).get("en", {}).get("value", disease),
            "description": entity_payload.get("descriptions", {}).get("en", {}).get("value", ""),
            "aliases": alias_values,
            "url": f"https://www.wikidata.org/wiki/{entity_id}",
        }
        latency = int((time.perf_counter() - start) * 1000)
        return SourcePayload("Wikidata", "ok", latency, payload, "entity facts loaded")
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return SourcePayload("Wikidata", "error", latency, {}, str(exc))


async def fetch_clinicaltrials_snapshot(disease: str, timeout_seconds: float = 8.0) -> SourcePayload:
    start = time.perf_counter()
    try:
        params = {
            "query.term": disease,
            "pageSize": 20,
            "format": "json",
        }
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            body = await _get_json(client, CLINICALTRIALS_API, params=params)

        studies = body.get("studies", []) if isinstance(body, dict) else []
        total = int(body.get("totalCount", len(studies))) if isinstance(body, dict) else len(studies)

        recruiting = 0
        for study in studies:
            overall = (
                study.get("protocolSection", {})
                .get("statusModule", {})
                .get("overallStatus", "")
                .lower()
            )
            if "recruit" in overall:
                recruiting += 1

        payload = {
            "total_studies": total,
            "sampled_studies": len(studies),
            "recruiting_in_sample": recruiting,
            "query": disease,
        }
        latency = int((time.perf_counter() - start) * 1000)
        return SourcePayload("ClinicalTrials.gov", "ok", latency, payload, "trial snapshot loaded")
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return SourcePayload("ClinicalTrials.gov", "error", latency, {}, str(exc))


async def fetch_medlineplus_snapshot(disease: str, timeout_seconds: float = 8.0) -> SourcePayload:
    start = time.perf_counter()
    try:
        params = {"db": "healthTopics", "term": disease}
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            text = await _get_text(client, MEDLINEPLUS_SEARCH, params=params)

        root = ET.fromstring(text)
        docs = root.findall(".//document")
        first = docs[0] if docs else None

        payload = {
            "result_count": len(docs),
            "top_title": (first.findtext("content[@name='title']") if first is not None else "") or "",
            "top_url": (first.findtext("content[@name='url']") if first is not None else "") or "",
        }
        latency = int((time.perf_counter() - start) * 1000)
        return SourcePayload("MedlinePlus", "ok", latency, payload, "health topic snapshot loaded")
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return SourcePayload("MedlinePlus", "error", latency, {}, str(exc))


async def fetch_openalex_snapshot(disease: str, timeout_seconds: float = 8.0) -> SourcePayload:
    start = time.perf_counter()
    try:
        params = {
            "filter": f"title.search:{disease}",
            "per-page": 1,
        }
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            body = await _get_json(client, OPENALEX_WORKS, params=params)

        payload = {
            "works_count": body.get("meta", {}).get("count", 0),
            "query": disease,
            "url": f"https://api.openalex.org/works?filter=title.search:{quote(disease)}",
        }
        latency = int((time.perf_counter() - start) * 1000)
        return SourcePayload("OpenAlex", "ok", latency, payload, "literature count loaded")
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return SourcePayload("OpenAlex", "error", latency, {}, str(exc))
