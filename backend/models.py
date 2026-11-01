"""Shared settings and data models for the API, crawler, and reports."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", str(Path(__file__).resolve().parent.parent))


def _as_dict(value: Any) -> dict:
    """Normalize optional JSON objects before they reach dict operations."""
    return value if isinstance(value, dict) else {}


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ──────────────────────────────────────────────
# Research Plan
# ──────────────────────────────────────────────

class ResearchPlan(BaseModel):
    topic: str
    constraints: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    source_categories: list[str] = Field(default_factory=list)
    ranking_priorities: list[str] = Field(default_factory=list)
    max_pages: int = 20
    stopping_criteria: list[str] = Field(default_factory=list)
    extraction_schema: dict[str, str] = Field(default_factory=dict)

    @field_validator("extraction_schema", mode="before")
    @classmethod
    def normalize_extraction_schema(cls, value: Any) -> dict:
        return _as_dict(value)


# ──────────────────────────────────────────────
# Evidence & Extracted Records
# ──────────────────────────────────────────────

class EvidenceSnippet(BaseModel):
    text: str
    source_url: str
    field_name: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExtractedRecord(BaseModel):
    url: str
    domain: str
    title: str
    main_text: str
    page_summary: str = ""
    relevant_text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    published_date: Optional[str] = None
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    evidence_snippets: list[EvidenceSnippet] = Field(default_factory=list)
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    field_coverage: dict[str, bool] = Field(default_factory=dict)
    dynamic_fields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", "field_coverage", "dynamic_fields", mode="before")
    @classmethod
    def normalize_record_mappings(cls, value: Any) -> dict:
        return _as_dict(value)


# ──────────────────────────────────────────────
# Crawl State
# ──────────────────────────────────────────────

class CandidateURL(BaseModel):
    url: str
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    depth: int = 0
    source: str = "discovery"   # discovery | link | seed | fallback
    reason: str = ""
    title: Optional[str] = None
    snippet: Optional[str] = None


class CrawlState(BaseModel):
    task_id: str
    objective: str
    plan: ResearchPlan
    candidate_urls: list[CandidateURL] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    failed_urls: list[str] = Field(default_factory=list)
    records: list[ExtractedRecord] = Field(default_factory=list)
    fallback_record: Optional[ExtractedRecord] = None
    missing_fields: list[str] = Field(default_factory=list)
    conflicting_fields: list[str] = Field(default_factory=list)
    pages_crawled: int = 0
    status: TaskStatus = TaskStatus.PENDING
    stopping_reason: Optional[str] = None
    progress_percent: float = 0.0


# ──────────────────────────────────────────────
# Ranked Result
# ──────────────────────────────────────────────

class RankedResult(BaseModel):
    rank: int
    url: str
    title: str
    domain: str
    final_score: float
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    score_explanation: str = ""
    dynamic_fields: dict[str, Any] = Field(default_factory=dict)
    evidence_snippets: list[EvidenceSnippet] = Field(default_factory=list)
    published_date: Optional[str] = None

    @field_validator("score_breakdown", "dynamic_fields", mode="before")
    @classmethod
    def normalize_result_mappings(cls, value: Any) -> dict:
        return _as_dict(value)


class ReportChunk(BaseModel):
    title: str
    text: str
    source_url: str = ""


# ──────────────────────────────────────────────
# Research Report
# ──────────────────────────────────────────────

class ResearchReport(BaseModel):
    task_id: str
    objective: str
    executive_summary: str
    report_narrative: str = ""
    report_chunks: list[ReportChunk] = Field(default_factory=list)
    top_source_title: str = ""
    top_source_url: str = ""
    relevant_source_text: str = ""
    bart_summary: str = ""
    methodology: str
    ranked_results: list[RankedResult] = Field(default_factory=list)
    comparison_table: list[dict[str, Any]] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conflicting_information: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    crawl_stats: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("crawl_stats", mode="before")
    @classmethod
    def normalize_crawl_stats(cls, value: Any) -> dict:
        return _as_dict(value)


# ──────────────────────────────────────────────
# API Request / Response Models
# ──────────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    objective: str = Field(..., min_length=10, max_length=2000)
    max_pages: int = Field(default=20, ge=3, le=100)
    crawl_depth: int = Field(default=2, ge=1, le=5)


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    objective: str
    created_at: datetime
    updated_at: datetime
    progress_percent: float = 0.0
    pages_crawled: int = 0
    stopping_reason: Optional[str] = None
    plan: Optional[ResearchPlan] = None


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int


class ProgressResponse(BaseModel):
    task_id: str
    status: TaskStatus
    progress_percent: float
    pages_crawled: int
    candidate_urls_count: int
    records_count: int
    missing_fields: list[str]
    stopping_reason: Optional[str]
    recent_urls: list[str] = Field(default_factory=list)


class SourcesResponse(BaseModel):
    task_id: str
    visited_urls: list[str]
    failed_urls: list[str]
    candidate_urls: list[CandidateURL]
    records: list[ExtractedRecord]


class ReportResponse(BaseModel):
    task_id: str
    report: Optional[ResearchReport]
    ranked_results: list[RankedResult]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///./research.db"
    frontend_url: str = "http://localhost:5173"
    app_port: int = 8000
    default_max_pages: int = 20
    default_crawl_depth: int = 2
    bart_enabled: bool = True
    bart_model: str = "auto"
    bart_input_tokens: int = 900
    bart_summary_tokens: int = 120


settings = Settings()
