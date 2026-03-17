from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass
class GlobalStatsResult:
    source: str
    status: str
    latency_ms: int
    records: int
    message: str


async def fetch_global_stats(timeout_seconds: float = 5.0) -> GlobalStatsResult:
    start = time.perf_counter()
    url = "https://disease.sh/v3/covid-19/all"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        latency = int((time.perf_counter() - start) * 1000)
        sample = payload.get("todayCases", 0)
        return GlobalStatsResult(
            source="Disease.sh Global",
            status="ok",
            latency_ms=latency,
            records=1,
            message=f"live baseline reachable (todayCases={sample})",
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return GlobalStatsResult(
            source="Disease.sh Global",
            status="error",
            latency_ms=latency,
            records=0,
            message=str(exc),
        )
