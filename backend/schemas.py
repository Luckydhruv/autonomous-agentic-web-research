"""
schemas.py – Pydantic models for API requests, responses, and internal data structures.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


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
    metadata: dict[str, Any] = Field(default_factory=dict)
    published_date: Optional[str] = None
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    evidence_snippets: list[EvidenceSnippet] = Field(default_factory=list)
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    field_coverage: dict[str, bool] = Field(default_factory=dict)
    dynamic_fields: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────
# Crawl State
# ──────────────────────────────────────────────

class CandidateURL(BaseModel):
    url: str
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    depth: int = 0
    source: str = "discovery"   # discovery | link | seed | fallback_search
    reason: str = ""


class CrawlState(BaseModel):
    task_id: str
    objective: str
    plan: ResearchPlan
    candidate_urls: list[CandidateURL] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    failed_urls: list[str] = Field(default_factory=list)
    records: list[ExtractedRecord] = Field(default_factory=list)
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


# ──────────────────────────────────────────────
# Research Report
# ──────────────────────────────────────────────

class ResearchReport(BaseModel):
    task_id: str
    objective: str
    executive_summary: str
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


# ──────────────────────────────────────────────
# API Request / Response Models
# ──────────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    objective: str = Field(..., min_length=10, max_length=2000)
    max_pages: int = Field(default=20, ge=10, le=100)  # minimum 10 pages enforced
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
