from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .clinical_icd import search_icd10


@dataclass
class AilmentCatalogResult:
    source: str
    status: str
    latency_ms: int
    items: list[str]
    message: str = ""


_CATALOG_CACHE: dict[str, object] = {
    "expires_at": 0.0,
    "items": [],
}


async def fetch_ailment_catalog(refresh: bool = False, per_letter_limit: int = 300, ttl_seconds: int = 3600) -> AilmentCatalogResult:
    now = time.time()
    if not refresh and _CATALOG_CACHE["expires_at"] > now and _CATALOG_CACHE["items"]:
        return AilmentCatalogResult(
            source="NLM ClinicalTables ICD-10-CM (cached A-Z)",
            status="ok",
            latency_ms=0,
            items=list(_CATALOG_CACHE["items"]),
            message="cached",
        )

    start = time.perf_counter()
    letters = [chr(code) for code in range(ord("a"), ord("z") + 1)]
    tasks = [search_icd10(query=letter, limit=per_letter_limit, timeout_seconds=8.0) for letter in letters]
    results = await asyncio.gather(*tasks)

    names: set[str] = set()
    failed = 0
    for result in results:
        if result.status != "ok":
            failed += 1
            continue
        for item in result.items:
            name = item.get("name", "").strip()
            if name:
                names.add(name)

    sorted_names = sorted(names, key=lambda x: x.casefold())
    _CATALOG_CACHE["items"] = sorted_names
    _CATALOG_CACHE["expires_at"] = now + ttl_seconds

    latency = int((time.perf_counter() - start) * 1000)
    status = "ok" if failed == 0 else "partial"
    message = f"letters_failed={failed}" if failed else "all letter queries succeeded"

    return AilmentCatalogResult(
        source="NLM ClinicalTables ICD-10-CM (A-Z)",
        status=status,
        latency_ms=latency,
        items=sorted_names,
        message=message,
    )
