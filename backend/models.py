from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class YearlyCases(BaseModel):
    year: int
    cases: int


class OutbreakAlert(BaseModel):
    date: str
    source: str
    alert: str
    severity: Literal["low", "moderate", "high"] = "moderate"


class CountryCases(BaseModel):
    country: str
    cases: int


class Classification(BaseModel):
    disease: str
    icd: str
    disease_type: str = Field(alias="type")
    subtype: str


class GeneAssociation(BaseModel):
    symbol: str
    score: float
    summary: str


class TherapeuticInsight(BaseModel):
    drug: str
    mechanism: str
    who_essential: str
    guideline: str


class Epidemiology(BaseModel):
    region: str
    metric_label: str = "Cases"
    cases_by_year: list[YearlyCases]
    outbreak_alerts: list[OutbreakAlert]
    country_comparison: list[CountryCases]


class SourceStatus(BaseModel):
    source: str
    status: Literal["ok", "fallback", "error"]
    latency_ms: int | None = None
    records: int = 0
    message: str = ""
    category: str = "general"
    applicable: bool = True
    provenance: Literal["live", "cached", "fallback", "derived", "unavailable"] = "derived"


class ProvenanceSummary(BaseModel):
    live_sources: int
    fallback_sources: int
    unavailable_sources: int
    cached_sources: int
    applicable_sources: int
    has_fallback: bool
    has_unavailable: bool


class Analytics(BaseModel):
    trend_percent: float
    cagr_percent: float
    anomaly_years: list[int]
    confidence_score: float


class ObservatoryResponse(BaseModel):
    query_disease: str
    query_region: str
    cache_hit: bool = False
    classification: Classification
    epidemiology: Epidemiology
    genes: list[GeneAssociation]
    therapeutics: TherapeuticInsight
    analytics: Analytics
    source_status: list[SourceStatus]
    provenance_summary: ProvenanceSummary
    generated_at_utc: datetime
