"""
main.py – FastAPI application with all API routes.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import database
from agent import cancel_research_task, search_preview_record, start_research_task
from config import settings
from crawl4ai_client import Crawl4AIError, check_service
from ranking import rank_records
from offline_sources import cached_fallback_records
from discovery import discover_candidate_urls
from reporter import generate_report
from schemas import (
    CreateTaskRequest,
    ProgressResponse,
    RankedResult,
    ReportResponse,
    SourcesResponse,
    TaskListResponse,
    TaskResponse,
    TaskStatus,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Initialising database...")
    await database.init_db()
    log.info("Database ready.")
    yield


app = FastAPI(
    title="Autonomous Agentic Web Research System",
    description="An AI agent that autonomously converts research goals into adaptive crawling strategies.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS – allow the Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/config", tags=["system"])
async def get_config() -> dict:
    """Return safe configuration info for the frontend."""
    return {
        "ollama_enabled": settings.ollama_enabled,
        "ollama_model": settings.ollama_model,
        "crawler": "Crawl4AI local Docker API",
        "crawl4ai_configured": bool(settings.crawl4ai_api_token.strip()),
        "crawl4ai_api_base": settings.crawl4ai_api_base,
        "default_max_pages": settings.default_max_pages,
        "default_crawl_depth": settings.default_crawl_depth,
        "cloud_llm_enabled": settings.cloud_llm_enabled,
    }


# ──────────────────────────────────────────────
# Tasks
# ──────────────────────────────────────────────

@app.post("/api/tasks", response_model=TaskResponse, status_code=201, tags=["tasks"])
async def create_task(request: CreateTaskRequest) -> TaskResponse:
    """Create a new research task and start the autonomous agent."""
    if not settings.crawl4ai_api_token.strip():
        raise HTTPException(
            status_code=503,
            detail="Crawl4AI API token is not configured. Start the local Crawl4AI service and set CRAWL4AI_API_TOKEN in .env.",
        )
    try:
        await check_service()
    except Crawl4AIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    task_id = str(uuid.uuid4())
    task = await database.create_task(
        task_id=task_id,
        objective=request.objective,
        max_pages=request.max_pages,
        crawl_depth=request.crawl_depth,
    )

    # Launch background agent
    await start_research_task(
        task_id=task_id,
        objective=request.objective,
        max_pages=request.max_pages,
        crawl_depth=request.crawl_depth,
    )

    return TaskResponse(
        task_id=task.id,
        status=TaskStatus(task.status),
        objective=task.objective,
        created_at=task.created_at,
        updated_at=task.updated_at,
        progress_percent=0.0,
        pages_crawled=0,
    )


@app.get("/api/tasks", response_model=TaskListResponse, tags=["tasks"])
async def list_tasks() -> TaskListResponse:
    """Return all research tasks ordered by creation date."""
    tasks = await database.get_all_tasks()
    items = []
    for t in tasks:
        plan = None
        if t.plan_json:
            from schemas import ResearchPlan
            try:
                plan = ResearchPlan.model_validate_json(t.plan_json)
            except Exception:
                pass
        items.append(
            TaskResponse(
                task_id=t.id,
                status=TaskStatus(t.status),
                objective=t.objective,
                created_at=t.created_at,
                updated_at=t.updated_at,
                progress_percent=t.progress_percent or 0.0,
                pages_crawled=t.pages_crawled or 0,
                stopping_reason=t.stopping_reason,
                plan=plan,
            )
        )
    return TaskListResponse(tasks=items, total=len(items))


@app.get("/api/tasks/{task_id}", response_model=TaskResponse, tags=["tasks"])
async def get_task(task_id: str) -> TaskResponse:
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    plan = None
    if task.plan_json:
        from schemas import ResearchPlan
        try:
            plan = ResearchPlan.model_validate_json(task.plan_json)
        except Exception:
            pass

    return TaskResponse(
        task_id=task.id,
        status=TaskStatus(task.status),
        objective=task.objective,
        created_at=task.created_at,
        updated_at=task.updated_at,
        progress_percent=task.progress_percent or 0.0,
        pages_crawled=task.pages_crawled or 0,
        stopping_reason=task.stopping_reason,
        plan=plan,
    )


@app.post("/api/tasks/{task_id}/cancel", tags=["tasks"])
async def cancel_task(task_id: str) -> dict:
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (TaskStatus.RUNNING, TaskStatus.PLANNING, TaskStatus.PENDING):
        raise HTTPException(status_code=400, detail=f"Task is already {task.status}")
    await cancel_research_task(task_id)
    return {"message": "Cancellation requested", "task_id": task_id}


# ──────────────────────────────────────────────
# Progress
# ──────────────────────────────────────────────

@app.get("/api/tasks/{task_id}/progress", response_model=ProgressResponse, tags=["tasks"])
async def get_progress(task_id: str) -> ProgressResponse:
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    state = await database.load_state(task_id)
    if state:
        return ProgressResponse(
            task_id=task_id,
            status=TaskStatus(task.status),
            progress_percent=state.progress_percent,
            pages_crawled=state.pages_crawled,
            candidate_urls_count=len(state.candidate_urls),
            records_count=(
                len(rank_records(state.records, state.plan))
                or int(state.fallback_record is not None)
            ),
            missing_fields=state.missing_fields,
            stopping_reason=state.stopping_reason,
            recent_urls=state.visited_urls[-5:] if state.visited_urls else [],
        )

    return ProgressResponse(
        task_id=task_id,
        status=TaskStatus(task.status),
        progress_percent=task.progress_percent or 0.0,
        pages_crawled=task.pages_crawled or 0,
        candidate_urls_count=0,
        records_count=0,
        missing_fields=[],
        stopping_reason=task.stopping_reason,
        recent_urls=[],
    )


# ──────────────────────────────────────────────
# Sources
# ──────────────────────────────────────────────

@app.get("/api/tasks/{task_id}/sources", response_model=SourcesResponse, tags=["results"])
async def get_sources(task_id: str) -> SourcesResponse:
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    state = await database.load_state(task_id)
    if not state:
        return SourcesResponse(
            task_id=task_id,
            visited_urls=[],
            failed_urls=[],
            candidate_urls=[],
            records=[],
        )

    visible_ranking = rank_records(state.records, state.plan)
    records_by_url = {record.url: record for record in state.records}
    visible_records = [records_by_url[result.url] for result in visible_ranking if result.url in records_by_url]
    if not visible_records and state.fallback_record:
        visible_records = [state.fallback_record]

    return SourcesResponse(
        task_id=task_id,
        visited_urls=state.visited_urls,
        failed_urls=state.failed_urls,
        candidate_urls=state.candidate_urls[:20],
        records=visible_records,
    )


# ──────────────────────────────────────────────
# Report & Results
# ──────────────────────────────────────────────

@app.get("/api/tasks/{task_id}/report", response_model=ReportResponse, tags=["results"])
async def get_report(task_id: str) -> ReportResponse:
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    report = await database.load_report(task_id)
    state = await database.load_state(task_id)

    ranked: list[RankedResult] = []
    if report:
        ranked = report.ranked_results
    elif state and state.records:
        # Surface provisional results while a task is still running or if report
        # generation did not finish; never show an empty list when pages were retained.
        ranked = rank_records(state.records, state.plan)
    elif state and state.fallback_record:
        ranked = rank_records([state.fallback_record], state.plan)

    return ReportResponse(
        task_id=task_id,
        report=report,
        ranked_results=ranked,
    )


@app.post("/api/tasks/{task_id}/report/regenerate", response_model=ReportResponse, tags=["results"])
async def regenerate_report(task_id: str) -> ReportResponse:
    """Re-rank saved pages and rebuild the report without re-crawling websites."""
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in (TaskStatus.RUNNING, TaskStatus.PLANNING, TaskStatus.PENDING):
        raise HTTPException(status_code=409, detail="Wait until crawling finishes before rebuilding the report")

    state = await database.load_state(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="No saved crawl data is available")
    has_cached_records = any(
        isinstance(record.metadata, dict)
        and record.metadata.get("retrieval_mode") == "cached_fallback"
        for record in state.records
    )
    has_preview_records = any(
        isinstance(record.metadata, dict)
        and record.metadata.get("retrieval_mode") == "search_preview"
        for record in state.records
    )
    if not state.records or has_cached_records:
        cached_records = cached_fallback_records(state.plan)
        if cached_records:
            state.records = cached_records
            state.fallback_record = cached_records[0]
            state.stopping_reason = "Live page fetches failed; labeled cached source text used"
            from evaluator import evaluate_state
            state = evaluate_state(state)

    if not state.records or has_preview_records:
        # Recover older empty tasks using a fresh no-key search and retain the
        # source snippets when their full pages remain inaccessible.
        refreshed_candidates = await discover_candidate_urls(state.plan)
        state.candidate_urls.extend(refreshed_candidates)
        previews = [
            preview for candidate in refreshed_candidates
            if (preview := search_preview_record(candidate, state.plan)) is not None
        ]
        relevant_previews = [
            record for record in previews
            if record.relevance_score >= 0.15
            and float(record.metadata.get("preview_quality", 0.0)) >= 0.38
        ]
        if not relevant_previews and previews:
            relevant_previews = [max(previews, key=lambda record: record.relevance_score)]
        if relevant_previews:
            state.records = relevant_previews[:12]
            state.fallback_record = max(relevant_previews, key=lambda record: record.relevance_score)
            state.stopping_reason = "Live article text unavailable; search-result previews used"
            from evaluator import evaluate_state
            state = evaluate_state(state)

    if not state.records and state.fallback_record:
        state.records.append(state.fallback_record)

    ranked = rank_records(state.records, state.plan)
    report = await generate_report(state, ranked)
    await database.save_report(task_id, report)
    await database.save_state(task_id, state)
    return ReportResponse(task_id=task_id, report=report, ranked_results=ranked)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.app_port, reload=True)
