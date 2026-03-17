from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from ..config import settings


@dataclass
class FeedResult:
    source: str
    status: str
    alerts: list[dict]
    latency_ms: int
    message: str = ""


@dataclass
class MultiFeedResult:
    alerts: list[dict]
    status_rows: list[FeedResult]


def _severity_from_text(text: str) -> str:
    blob = text.lower()
    if any(term in blob for term in ["death", "fatal", "critical", "outbreak", "cluster"]):
        return "high"
    if any(term in blob for term in ["increase", "rising", "spread", "alert"]):
        return "moderate"
    return "low"


def _parse_rss_items(xml_text: str, source_label: str, disease: str, region: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    candidates: list[dict] = []
    disease_q = disease.lower().strip()
    region_q = region.lower().strip()

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or item.findtext("published") or "").strip()
        description = (item.findtext("description") or "").strip()
        blob = f"{title} {description}".lower()
        if disease_q and disease_q not in blob and region_q and region_q not in blob:
            continue
        if disease_q and region_q and disease_q not in blob and region_q not in blob:
            continue
        if not disease_q and not region_q:
            pass

        candidates.append(
            {
                "date": pub_date[:32] if pub_date else "unknown",
                "source": source_label,
                "alert": title or "Untitled alert",
                "severity": _severity_from_text(blob),
            }
        )
    return candidates


async def _fetch_feed(feed_url: str, source_label: str, disease: str, region: str, timeout_seconds: float = 6.0) -> FeedResult:
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(feed_url, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
        latency = int((time.perf_counter() - start) * 1000)
        candidates = _parse_rss_items(response.text, source_label=source_label, disease=disease, region=region)

        return FeedResult(
            source=source_label,
            status="ok",
            alerts=candidates[:6],
            latency_ms=latency,
            message="live feed queried",
        )
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - start) * 1000)
        return FeedResult(
            source=source_label,
            status="error",
            alerts=[],
            latency_ms=latency,
            message=str(exc),
        )


async def fetch_who_outbreak_feed(feed_url: str, disease: str, region: str, timeout_seconds: float = 6.0) -> FeedResult:
    return await _fetch_feed(feed_url, "WHO DON RSS", disease, region, timeout_seconds)


async def fetch_promed_feed(disease: str, region: str, timeout_seconds: float = 6.0) -> FeedResult:
    return await _fetch_feed(settings.promed_feed_url, "ProMED RSS", disease, region, timeout_seconds)


async def fetch_healthmap_feed(disease: str, region: str, timeout_seconds: float = 6.0) -> FeedResult:
    return await _fetch_feed(settings.healthmap_feed_url, "HealthMap RSS", disease, region, timeout_seconds)


async def fetch_outbreak_feeds(disease: str, region: str) -> MultiFeedResult:
    rows = await asyncio.gather(
        fetch_who_outbreak_feed(settings.outbreak_feed_url, disease=disease, region=region),
        fetch_promed_feed(disease=disease, region=region),
        fetch_healthmap_feed(disease=disease, region=region),
    )
    alerts: list[dict] = []
    for row in rows:
        alerts.extend(row.alerts)
    deduped = {(a["source"], a["date"], a["alert"]): a for a in alerts}
    merged = sorted(deduped.values(), key=lambda x: x["date"], reverse=True)
    return MultiFeedResult(alerts=merged[:20], status_rows=list(rows))
