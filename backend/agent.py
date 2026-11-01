"""
agent.py – The adaptive research agent controller.
This is the core innovation: an autonomous crawl loop that:
  1. Selects the highest-priority URL
  2. Fetches and extracts content
  3. Updates research state
  4. Reprioritises remaining URLs based on missing info
  5. Decides to continue or stop
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

import database
from config import settings
from crawler import fetch_page
from discovery import (
    discover_candidate_urls,
    normalize_url,
    score_link_relevance,
    search_result_matches_topic,
)
from evaluator import (
    compute_field_coverage,
    compute_relevance,
    evaluate_state,
    is_duplicate,
    should_stop,
)
from content_utils import extract_relevant_text, has_useful_content, is_non_article_page
from offline_sources import cached_fallback_records
from extractor import extract_page
from planner import create_research_plan
from ranking import rank_records
from reporter import generate_report
from schemas import (
    CandidateURL, CrawlState, EvidenceSnippet, ExtractedRecord, ResearchPlan, TaskStatus
)

log = logging.getLogger(__name__)

# In-memory registry of active agent tasks (task_id -> asyncio.Task)
_active_tasks: dict[str, asyncio.Task] = {}


# ──────────────────────────────────────────────
# URL reprioritisation
# ──────────────────────────────────────────────

def _reprioritise_queue(state: CrawlState) -> CrawlState:
    """
    Reorder the candidate URL queue based on:
    - Current missing fields (boost URLs that might cover them)
    - Source diversity (penalise domains already heavily visited)
    """
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

        # Penalise if we've already visited too many pages from this domain
        domain_count = visited_domains.get(domain, 0)
        domain_penalty = min(domain_count * 0.05, 0.3)

        # Boost if URL text hints at missing fields
        url_lower = candidate.url.lower()
        field_boost = 0.0
        for field in state.missing_fields:
            if field.lower() in url_lower:
                field_boost += 0.15

        candidate.priority = round(
            min(max(candidate.priority + field_boost - domain_penalty, 0.0), 1.0),
            3,
        )

    # Sort queue by priority descending
    state.candidate_urls.sort(key=lambda c: c.priority, reverse=True)
    return state


# ──────────────────────────────────────────────
# Single crawl step
# ──────────────────────────────────────────────

def search_preview_record(candidate: CandidateURL, plan: ResearchPlan) -> ExtractedRecord | None:
    """Build a labeled source record from a search-engine result preview."""
    title = (candidate.title or "").strip()
    snippet = (candidate.snippet or "").strip()
    if not title or not snippet or len(snippet.split()) < 8:
        return None
    if (
        candidate.source != "crawl4ai_search_fallback"
        and not search_result_matches_topic(candidate.url, title, snippet, plan)
    ):
        return None
    lower_snippet = snippet.casefold()
    generic_copy = any(
        phrase in lower_snippet
        for phrase in (
            "this guide will walk you through",
            "learn more about our",
            "visit our website to learn",
            "we are here to help you",
            "discover everything you need to know",
        )
    )
    if re.search(r"\bplacements?\b", plan.topic, re.IGNORECASE):
        specific_detail = bool(
            extract_relevant_text(f"{title}\n{snippet}", plan.topic, title)
        )
    else:
        specific_detail = bool(
            re.search(r"\b\d+\b", snippet)
            or re.search(r"\b(examples?|questions?|steps?|principles|checklist|star method|case study)\b", lower_snippet)
        )
    preview_quality = 0.15 if generic_copy else min(
        0.85, 0.25 + min(len(snippet.split()), 40) * 0.01 + (0.25 if specific_detail else 0.0)
    )
    url = candidate.url
    preview_text = f"{title}\n{snippet}"
    host = urlparse(url).netloc.lower()
    preview = ExtractedRecord(
        url=url,
        domain=host,
        title=title[:300],
        main_text=preview_text[:3000],
        relevant_text=preview_text[:3000],
        metadata={
            "retrieval_mode": "search_preview",
            "preview_quality": round(preview_quality, 2),
            "actionable_detail": specific_detail and not generic_copy,
            "notice": "Search-result preview; the linked article could not be fetched.",
        },
        evidence_snippets=[
            EvidenceSnippet(text=snippet[:500], source_url=url, confidence=0.4)
        ],
    )
    preview.relevance_score = compute_relevance(preview, plan)
    return preview


def _keep_preview(state: CrawlState, candidate: CandidateURL) -> None:
    preview = search_preview_record(candidate, state.plan)
    if not preview:
        return
    quality = float(preview.metadata.get("preview_quality", 0.0))
    if preview.relevance_score >= 0.15 and quality >= 0.38 and not any(r.url == preview.url for r in state.records):
        state.records.append(preview)
    if state.fallback_record is None or preview.relevance_score > state.fallback_record.relevance_score:
        state.fallback_record = preview

async def _crawl_step(state: CrawlState, depth_limit: int) -> CrawlState:
    """Process one URL from the priority queue and update the state."""
    if not state.candidate_urls:
        return state

    # Pop highest-priority URL
    candidate = state.candidate_urls.pop(0)
    url = candidate.url

    # Skip if already visited
    norm = normalize_url(url)
    if norm in [normalize_url(v) for v in state.visited_urls]:
        log.debug("Already visited, skipping: %s", url)
        return state

    state.visited_urls.append(url)
    # Fetch page
    result = await fetch_page(url)
    if not result.ok:
        log.info("Fetch failed [%s]: %s", result.error, url)
        state.failed_urls.append(url)
        # A search-result preview is still source text tied to the original
        # article URL. Keep it as a clearly labeled low-confidence result so a
        # blocked article does not make the whole task appear empty.
        _keep_preview(state, candidate)
        return state

    state.pages_crawled += 1

    # Extract structured record
    record = await extract_page(
        url=url,
        final_url=result.final_url,
        html=result.html,
        plan=state.plan,
    )

    # Score relevance
    record.relevance_score = compute_relevance(record, state.plan)

    # Duplicate check
    if is_duplicate(record, state.records):
        log.debug("Duplicate content, skipping: %s", url)
        return state

    useful_text = record.relevant_text or record.main_text
    if has_useful_content(useful_text) and (
        state.fallback_record is None
        or record.relevance_score > state.fallback_record.relevance_score
    ):
        state.fallback_record = record

    # Retain topical pages with substantive extracted text. Field labels appearing
    # in a navigation menu are not enough to make a page a research result.
    if (
        record.relevance_score >= 0.30
        and has_useful_content(record.relevant_text)
        and not is_non_article_page(record.url, record.title)
    ):
        state.records.append(record)
        log.info(
            "  → Relevant (score=%.2f, coverage=%.2f, fields=%s)",
            record.relevance_score,
            compute_field_coverage(record, state.plan),
            list(record.dynamic_fields.keys())[:5],
        )
    else:
        log.debug("  → Low relevance (%.2f), not adding to records", record.relevance_score)
        # A fetched page may be a consent/blocked page with no usable article
        # body. Retain its matching search preview as a last-resort source row.
        _keep_preview(state, candidate)

    # Use internal links extracted by Crawl4AI to expand the frontier up to
    # the requested depth. No local HTML link parser or direct HTTP fetches.
    if candidate.depth < depth_limit:
        visited = {normalize_url(value) for value in state.visited_urls}
        queued = {normalize_url(item.url) for item in state.candidate_urls}
        added = 0
        for link, anchor in result.links or []:
            normalized = normalize_url(link)
            if normalized in visited or normalized in queued:
                continue
            if not search_result_matches_topic(link, anchor, "", state.plan):
                continue
            priority = score_link_relevance(link, anchor, state.plan)
            if priority < 0.25:
                continue
            state.candidate_urls.append(CandidateURL(
                url=link,
                priority=round(priority * 0.8, 3),
                depth=candidate.depth + 1,
                source="crawl4ai_link",
                reason=f"Relevant internal link discovered by Crawl4AI on {url[:80]}",
                title=anchor or None,
            ))
            queued.add(normalized)
            added += 1
            if added >= 8:
                break

    return state


# ──────────────────────────────────────────────
# Main agent loop
# ──────────────────────────────────────────────

async def run_research_agent(task_id: str, objective: str, max_pages: int, crawl_depth: int) -> None:
    """
    Full autonomous research loop.
    Designed to run as a background asyncio task.
    """
    log.info("=== Research Agent START: %s ===", task_id)
    try:
        # Phase 1: Planning
        await database.update_task_status(task_id, TaskStatus.PLANNING, progress_percent=2.0)
        plan = await create_research_plan(objective, max_pages)
        await database.save_plan(task_id, plan)
        log.info("Plan created: topic=%s, fields=%s", plan.topic, plan.required_fields)

        # Phase 2: Source Discovery
        await database.update_task_status(task_id, TaskStatus.RUNNING, progress_percent=5.0)
        initial_candidates = await discover_candidate_urls(plan)

        # Initialise crawl state
        state = CrawlState(
            task_id=task_id,
            objective=objective,
            plan=plan,
            candidate_urls=initial_candidates,
            missing_fields=list(plan.required_fields),
            status=TaskStatus.RUNNING,
        )
        # Publish the discovered queue immediately so the UI does not look
        # empty while it is fetching and extracting the first source.
        await database.save_state(task_id, state)

        if not initial_candidates:
            log.warning("No initial URLs discovered. Using search queries as fallback seeds.")
            # If no search results, we can't proceed meaningfully
            state.stopping_reason = (
                "Crawl4AI crawled the search pages but found no usable source links. "
                "Check the local Crawl4AI service and try a broader research objective."
            )
            state.status = TaskStatus.COMPLETED

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

            # Should we stop?
            stop, reason = should_stop(state)
            if stop:
                state.stopping_reason = reason
                state.status = TaskStatus.COMPLETED
                log.info("Stopping: %s", reason)
                break

            # One crawl step
            state = await _crawl_step(state, crawl_depth)

            # Evaluate state (missing fields, conflicts)
            state = evaluate_state(state)

            # Update progress
            progress = min(99.0, (state.pages_crawled / max(plan.max_pages, 1)) * 85 + 10)
            state.progress_percent = round(progress, 1)

            # Persist each crawl step so sources and results appear as soon as
            # they are found, including when the first few page fetches fail.
            await database.save_state(task_id, state)

        # Phase 4: Ranking
        if not state.records and state.fallback_record is not None:
            log.info("No page met the topical threshold; keeping the best readable page as a fallback result")
            state.records.append(state.fallback_record)
        if not state.records:
            cached_records = cached_fallback_records(plan)
            if cached_records:
                log.warning("Live discovery/fetching produced no records; using labeled cached source text")
                state.records.extend(cached_records)
                state.fallback_record = cached_records[0]
                state.stopping_reason = "Live page fetches failed; labeled cached source text used"
                state = evaluate_state(state)
        log.info("Ranking %d collected records...", len(state.records))
        ranked = rank_records(state.records, plan)

        # Phase 5: Report generation
        log.info("Generating report...")
        report = await generate_report(state, ranked)
        await database.save_report(task_id, report)

        # Final state save
        state.status = TaskStatus.COMPLETED if state.status != TaskStatus.CANCELLED else TaskStatus.CANCELLED
        state.progress_percent = 100.0
        await database.save_state(task_id, state)
        await database.update_task_status(
            task_id,
            state.status,
            progress_percent=100.0,
            pages_crawled=state.pages_crawled,
            stopping_reason=state.stopping_reason,
        )
        log.info("=== Research Agent DONE: %s (%d records, %d ranked) ===", task_id, len(state.records), len(ranked))

    except Exception as exc:
        log.exception("Agent failed for task %s: %s", task_id, exc)
        await database.update_task_status(
            task_id, TaskStatus.FAILED,
            stopping_reason=f"Internal error: {str(exc)[:200]}"
        )
    finally:
        _active_tasks.pop(task_id, None)


# ──────────────────────────────────────────────
# Task management
# ──────────────────────────────────────────────

async def start_research_task(
    task_id: str, objective: str, max_pages: int, crawl_depth: int
) -> None:
    """Spawn the agent as a background asyncio task."""
    coro = run_research_agent(task_id, objective, max_pages, crawl_depth)
    task = asyncio.create_task(coro, name=f"research-{task_id}")
    _active_tasks[task_id] = task


async def cancel_research_task(task_id: str) -> bool:
    """Request cancellation of a running research task."""
    await database.update_task_status(task_id, TaskStatus.CANCELLED)
    task = _active_tasks.get(task_id)
    if task and not task.done():
        task.cancel()
        return True
    return False
