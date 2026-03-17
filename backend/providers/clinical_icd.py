from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


ICD_SEARCH_URL = "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"


@dataclass
class IcdSearchResult:
    source: str
    status: str
    latency_ms: int
    items: list[dict[str, str]]
    message: str = ""


async def search_icd10(query: str, limit: int = 20, timeout_seconds: float = 6.0) -> IcdSearchResult:
    start = time.perf_counter()
    try:
        params = {
            "sf": "code,name",
            "df": "code,name",
            "terms": query,
            "maxList": str(limit),
        }
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(ICD_SEARCH_URL, params=params)
            response.raise_for_status()
            payload = response.json()

        latency = int((time.perf_counter() - start) * 1000)
        rows = payload[3] if isinstance(payload, list) and len(payload) > 3 else []
        items: list[dict[str, str]] = []
        for row in rows:
            if isinstance(row, list) and len(row) >= 2:
                items.append({"code": str(row[0]), "name": str(row[1])})

        return IcdSearchResult(
            source="NLM ClinicalTables ICD-10-CM",
            status="ok",
            latency_ms=latency,
            items=items,
            message="icd search queried",
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return IcdSearchResult(
            source="NLM ClinicalTables ICD-10-CM",
            status="error",
            latency_ms=latency,
            items=[],
            message=str(exc),
        )
