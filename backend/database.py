"""Async SQLite storage for tasks, crawl state, and reports."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from models import settings
from models import CrawlState, ResearchPlan, ResearchReport, TaskStatus


# ──────────────────────────────────────────────
# Engine & Session
# ──────────────────────────────────────────────

# Ensure aiosqlite driver for sqlite URLs
_db_url = settings.database_url
if _db_url.startswith("sqlite:///") and "aiosqlite" not in _db_url:
    _db_url = _db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

engine = create_async_engine(_db_url, echo=False)
Session = async_sessionmaker(engine, expire_on_commit=False)


# ──────────────────────────────────────────────
# ORM Models
# ──────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class TaskORM(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True)
    objective = Column(Text, nullable=False)
    status = Column(String, default=TaskStatus.PENDING)
    progress_percent = Column(Float, default=0.0)
    pages_crawled = Column(Integer, default=0)
    max_pages = Column(Integer, default=20)
    crawl_depth = Column(Integer, default=2)
    stopping_reason = Column(String, nullable=True)
    plan_json = Column(Text, nullable=True)           # JSON serialised ResearchPlan
    state_json = Column(Text, nullable=True)          # JSON serialised CrawlState
    report_json = Column(Text, nullable=True)         # JSON serialised ResearchReport
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ──────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────

async def init_db() -> None:
    """Create tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ──────────────────────────────────────────────
# CRUD
# ──────────────────────────────────────────────

async def create_task(
    task_id: str,
    objective: str,
    max_pages: int,
    crawl_depth: int,
) -> TaskORM:
    async with Session() as session:
        task = TaskORM(
            id=task_id,
            objective=objective,
            status=TaskStatus.PENDING,
            max_pages=max_pages,
            crawl_depth=crawl_depth,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


async def get_task(task_id: str) -> Optional[TaskORM]:
    async with Session() as session:
        return await session.get(TaskORM, task_id)


async def get_all_tasks() -> list[TaskORM]:
    async with Session() as session:
        result = await session.execute(
            select(TaskORM).order_by(TaskORM.created_at.desc())
        )
        return list(result.scalars().all())


async def update_task_status(
    task_id: str,
    status: TaskStatus,
    progress_percent: float = 0.0,
    pages_crawled: int = 0,
    stopping_reason: Optional[str] = None,
) -> None:
    async with Session() as session:
        task = await session.get(TaskORM, task_id)
        if task:
            task.status = status
            task.progress_percent = progress_percent
            task.pages_crawled = pages_crawled
            task.stopping_reason = stopping_reason
            task.updated_at = datetime.utcnow()
            await session.commit()


async def save_plan(task_id: str, plan: ResearchPlan) -> None:
    async with Session() as session:
        task = await session.get(TaskORM, task_id)
        if task:
            task.plan_json = plan.model_dump_json()
            task.updated_at = datetime.utcnow()
            await session.commit()


async def save_state(task_id: str, state: CrawlState) -> None:
    async with Session() as session:
        task = await session.get(TaskORM, task_id)
        if task:
            task.state_json = state.model_dump_json()
            task.progress_percent = state.progress_percent
            task.pages_crawled = state.pages_crawled
            task.status = state.status
            task.stopping_reason = state.stopping_reason
            task.updated_at = datetime.utcnow()
            await session.commit()


async def save_report(task_id: str, report: ResearchReport) -> None:
    async with Session() as session:
        task = await session.get(TaskORM, task_id)
        if task:
            task.report_json = report.model_dump_json()
            task.updated_at = datetime.utcnow()
            await session.commit()


async def load_state(task_id: str) -> Optional[CrawlState]:
    task = await get_task(task_id)
    if task and task.state_json:
        return CrawlState.model_validate_json(task.state_json)
    return None


async def load_report(task_id: str) -> Optional[ResearchReport]:
    task = await get_task(task_id)
    if task and task.report_json:
        return ResearchReport.model_validate_json(task.report_json)
    return None
