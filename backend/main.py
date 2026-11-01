"""FastAPI routes for creating and viewing research tasks."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import database
from models import (
    CreateTaskRequest, ProgressResponse, ReportResponse, SourcesResponse,
    TaskListResponse, TaskResponse, TaskStatus, settings,
)
from report import generate_report
from research import cancel_research_task, rank_records, start_research_task

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(_app):
    await database.init_db()
    yield


app = FastAPI(title="Web Research", version="2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


def _task_response(task) -> TaskResponse:
    plan = None
    if task.plan_json:
        try:
            from models import ResearchPlan
            plan = ResearchPlan.model_validate_json(task.plan_json)
        except Exception:
            pass
    return TaskResponse(
        task_id=task.id, status=TaskStatus(task.status), objective=task.objective,
        created_at=task.created_at, updated_at=task.updated_at,
        progress_percent=task.progress_percent or 0, pages_crawled=task.pages_crawled or 0,
        stopping_reason=task.stopping_reason, plan=plan,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/config")
async def config():
    return {"crawler": "Crawl4AI Python library", "crawl4ai_configured": True,
            "default_max_pages": settings.default_max_pages,
            "default_crawl_depth": settings.default_crawl_depth,
            "ollama_enabled": False, "cloud_llm_enabled": False}


@app.post("/api/tasks", response_model=TaskResponse, status_code=201)
async def create_task(request: CreateTaskRequest):
    task_id = str(uuid.uuid4())
    task = await database.create_task(task_id, request.objective, request.max_pages, request.crawl_depth)
    start_research_task(task_id, request.objective, request.max_pages, request.crawl_depth)
    return _task_response(task)


@app.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks():
    tasks = await database.get_all_tasks()
    return TaskListResponse(tasks=[_task_response(task) for task in tasks], total=len(tasks))


@app.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return _task_response(task)


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status not in (TaskStatus.PENDING, TaskStatus.PLANNING, TaskStatus.RUNNING):
        raise HTTPException(400, f"Task is already {task.status}")
    await cancel_research_task(task_id)
    return {"message": "Cancellation requested", "task_id": task_id}


@app.get("/api/tasks/{task_id}/progress", response_model=ProgressResponse)
async def progress(task_id: str):
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    state = await database.load_state(task_id)
    records = state.records if state else []
    return ProgressResponse(
        task_id=task_id, status=TaskStatus(task.status),
        progress_percent=state.progress_percent if state else task.progress_percent or 0,
        pages_crawled=state.pages_crawled if state else task.pages_crawled or 0,
        candidate_urls_count=len(state.candidate_urls) if state else 0,
        records_count=len(records) or int(bool(state and state.fallback_record)),
        missing_fields=state.missing_fields if state else [],
        stopping_reason=state.stopping_reason if state else task.stopping_reason,
        recent_urls=state.visited_urls[-5:] if state else [],
    )


@app.get("/api/tasks/{task_id}/sources", response_model=SourcesResponse)
async def sources(task_id: str):
    if not await database.get_task(task_id):
        raise HTTPException(404, "Task not found")
    state = await database.load_state(task_id)
    if not state:
        return SourcesResponse(task_id=task_id, visited_urls=[], failed_urls=[], candidate_urls=[], records=[])
    visible = state.records or ([state.fallback_record] if state.fallback_record else [])
    return SourcesResponse(task_id=task_id, visited_urls=state.visited_urls,
                           failed_urls=state.failed_urls, candidate_urls=state.candidate_urls[:20],
                           records=visible)


@app.get("/api/tasks/{task_id}/report", response_model=ReportResponse)
async def get_report(task_id: str):
    if not await database.get_task(task_id):
        raise HTTPException(404, "Task not found")
    report = await database.load_report(task_id)
    state = await database.load_state(task_id)
    ranked = report.ranked_results if report else rank_records(state.records, state.plan) if state else []
    return ReportResponse(task_id=task_id, report=report, ranked_results=ranked)


@app.post("/api/tasks/{task_id}/report/regenerate", response_model=ReportResponse)
async def regenerate_report(task_id: str):
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status in (TaskStatus.PENDING, TaskStatus.PLANNING, TaskStatus.RUNNING):
        raise HTTPException(409, "Wait for the crawl to finish")
    state = await database.load_state(task_id)
    if not state:
        raise HTTPException(404, "No saved crawl data")
    ranked = rank_records(state.records, state.plan)
    report = await generate_report(state, ranked)
    await database.save_report(task_id, report)
    return ReportResponse(task_id=task_id, report=report, ranked_results=ranked)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.app_port, reload=True)
