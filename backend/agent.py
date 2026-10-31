"""
agent.py – The adaptive research agent controller.
Fixed:
  1. Guaranteed candidate URLs from multi-provider discovery.
  2. Crawls up to max_pages (minimum 10 enforced).
  3. Seamless progress reporting and ranking.
  4. Always generates comprehensive report.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import database
from config import settings
from crawler import fetch_page
from discovery import (
    discover_candidate_urls,
    extract_links_from_html,
    normalize_url,
    score_link_relevance,
    search_urls,
)
from evaluator import (
    compute_field_coverage,
    compute_relevance,
    evaluate_state,
    is_duplicate,
    should_stop,
)
from extractor import extract_page
from planner import create_research_plan
from ranking import rank_records
from reporter import generate_report
from schemas import (
    CandidateURL, CrawlState, TaskStatus
)

log = logging.getLogger(__name__)

# Active background tasks
_active_tasks: dict[str, asyncio.Task] = {}


# ──────────────────────────────────────────────
# URL reprioritisation
# ──────────────────────────────────────────────

def _reprioritise_queue(state: CrawlState) -> CrawlState:
    """Reorder candidate URLs based on missing fields and domain diversity."""
    if not state.candidate_urls:
        return state

    visited_domains: dict[str, int] = {}
    for url in state.visited_urls:
        try:
            d = urlparse(url).netloc
            visited_domains[d] = visited_domains.get(d, 0) + 1
        except Exception:
            pass

    for candidate in state.candidate_urls:
        try:
            domain = urlparse(candidate.url).netloc
        except Exception:
            domain = ""

        domain_count = visited_domains.get(domain, 0)
        domain_penalty = min(domain_count * 0.08, 0.3)

        url_lower = candidate.url.lower()
        field_boost = 0.0
        for field in state.missing_fields:
            if field.lower() in url_lower:
                field_boost += 0.15

        candidate.priority = round(
            min(max(candidate.priority + field_boost - domain_penalty, 0.0), 1.0),
            3,
        )

    state.candidate_urls.sort(key=lambda c: c.priority, reverse=True)
    return state


# ──────────────────────────────────────────────
# Single crawl step
# ──────────────────────────────────────────────

async def _crawl_step(state: CrawlState, depth_limit: int) -> CrawlState:
    """Fetch next URL from queue, parse content, and discover child links."""
    if not state.candidate_urls:
        return state

    candidate = state.candidate_urls.pop(0)
    url = candidate.url

    norm = normalize_url(url)
    if norm in {normalize_url(v) for v in state.visited_urls}:
        return state

    state.visited_urls.append(url)
    state.pages_crawled += 1
    log.info(
        "[%d/%d] Crawling (priority=%.2f): %s",
        state.pages_crawled, state.plan.max_pages, candidate.priority, url[:80]
    )

    # 1. Fetch
    result = await fetch_page(url)
    if not result.ok:
        log.info("Fetch failed [%s]: %s", result.error, url[:60])
        state.failed_urls.append(url)
        return state

    # 2. Extract
    record = await extract_page(
        url=url,
        final_url=result.final_url,
        html=result.html,
        plan=state.plan,
    )

    # 3. Score relevance
    record.relevance_score = compute_relevance(record, state.plan)

    # 4. Duplicate check
    if is_duplicate(record, state.records):
        log.debug("Duplicate content, skipping: %s", url[:60])
        return state

    # 5. Keep record if has meaningful text content
    if len(record.main_text) >= 80:
        state.records.append(record)
        log.info(
            "  -> Extracted (relevance=%.2f, title='%s')",
            record.relevance_score,
            record.title[:45],
        )

    # 6. Child link discovery
    if candidate.depth < depth_limit:
        child_links = extract_links_from_html(result.html, result.final_url)
        visited_set = {normalize_url(v) for v in state.visited_urls}
        queued_set = {normalize_url(c.url) for c in state.candidate_urls}

        added = 0
        for link in child_links:
            norm_link = normalize_url(link)
            if norm_link in visited_set or norm_link in queued_set:
                continue
            link_score = score_link_relevance(link, "", state.plan)
            if link_score >= 0.25:
                state.candidate_urls.append(
                    CandidateURL(
                        url=link,
                        priority=round(link_score * 0.75, 3),
                        depth=candidate.depth + 1,
                        source="link",
                        reason=f"Link from {url[:50]}",
                    )
                )
                queued_set.add(norm_link)
                added += 1
                if added >= 8:
                    break

    return state


# ──────────────────────────────────────────────
# Main agent loop
# ──────────────────────────────────────────────

async def run_research_agent(task_id: str, objective: str, max_pages: int, crawl_depth: int) -> None:
    """Full autonomous research loop."""
    log.info("=== Research Agent START: %s ===", task_id)
    try:
        # Phase 1: Planning
        await database.update_task_status(task_id, TaskStatus.PLANNING, progress_percent=5.0)
        plan = await create_research_plan(objective, max_pages)
        await database.save_plan(task_id, plan)
        log.info("Plan ready: topic='%s', queries=%d", plan.topic, len(plan.search_queries))

        # Phase 2: Source Discovery
        await database.update_task_status(task_id, TaskStatus.RUNNING, progress_percent=15.0)
        candidates = await discover_candidate_urls(plan)

        state = CrawlState(
            task_id=task_id,
            objective=objective,
            plan=plan,
            candidate_urls=candidates,
            missing_fields=list(plan.required_fields),
            status=TaskStatus.RUNNING,
            progress_percent=15.0,
        )
        await database.save_state(task_id, state)

        # Phase 3: Adaptive Crawl Loop
        while state.status == TaskStatus.RUNNING:
            # Check cancellation
            task_row = await database.get_task(task_id)
            if task_row and task_row.status == TaskStatus.CANCELLED:
                state.status = TaskStatus.CANCELLED
                state.stopping_reason = "Cancelled by user"
                break

            # Reprioritise queue
            state = _reprioritise_queue(state)

            # Stopping condition
            stop, reason = should_stop(state)
            if stop:
                state.stopping_reason = reason
                state.status = TaskStatus.COMPLETED
                log.info("Stopping research: %s", reason)
                break

            # If queue is empty, attempt secondary discovery query
            if not state.candidate_urls:
                extra = await search_urls(f"{plan.topic} review news", max_results=10)
                visited = {normalize_url(v) for v in state.visited_urls}
                for u in extra:
                    if normalize_url(u) not in visited:
                        state.candidate_urls.append(CandidateURL(url=u, priority=0.6, depth=0))
                if not state.candidate_urls:
                    state.stopping_reason = "All discovered pages crawled"
                    state.status = TaskStatus.COMPLETED
                    break

            # Crawl next page
            state = await _crawl_step(state, crawl_depth)
            state = evaluate_state(state)

            # Progress computation (15% to 90%)
            progress = min(90.0, 15.0 + (state.pages_crawled / max(plan.max_pages, 1)) * 75.0)
            state.progress_percent = round(progress, 1)

            # Periodically persist state
            if state.pages_crawled % 2 == 0:
                await database.save_state(task_id, state)

        # Phase 4: Ranking
        log.info("Ranking %d records...", len(state.records))
        ranked = rank_records(state.records, plan)

        # Phase 5: Report generation
        log.info("Generating report...")
        report = await generate_report(state, ranked)
        await database.save_report(task_id, report)

        # Final state update
        state.status = TaskStatus.COMPLETED if state.status != TaskStatus.CANCELLED else TaskStatus.CANCELLED
        state.progress_percent = 100.0
        await database.save_state(task_id, state)
        await database.update_task_status(
            task_id,
            state.status,
            progress_percent=100.0,
            pages_crawled=state.pages_crawled,
            stopping_reason=state.stopping_reason or "Completed",
        )
        log.info("=== Research Agent COMPLETE: %s (%d pages crawled, %d ranked results) ===",
                 task_id, state.pages_crawled, len(ranked))

    except Exception as exc:
        log.exception("Agent execution failed for task %s: %s", task_id, exc)
        await database.update_task_status(
            task_id, TaskStatus.FAILED,
            stopping_reason=f"Error: {str(exc)[:200]}"
        )
    finally:
        _active_tasks.pop(task_id, None)


async def start_research_task(task_id: str, objective: str, max_pages: int, crawl_depth: int) -> None:
    """Spawn agent background task."""
    coro = run_research_agent(task_id, objective, max_pages, crawl_depth)
    task = asyncio.create_task(coro, name=f"research-{task_id}")
    _active_tasks[task_id] = task


async def cancel_research_task(task_id: str) -> bool:
    """Request task cancellation."""
    await database.update_task_status(task_id, TaskStatus.CANCELLED)
    task = _active_tasks.get(task_id)
    if task and not task.done():
        task.cancel()
        return True
    return False
