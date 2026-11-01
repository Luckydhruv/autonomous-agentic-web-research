"""Small Crawl4AI research pipeline: search, fetch, extract, rank, and save."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from urllib.parse import parse_qs, quote_plus, urlparse

import database
from models import (
    CandidateURL, CrawlState, EvidenceSnippet, ExtractedRecord, RankedResult,
    ResearchPlan, TaskStatus,
)
from report import generate_report
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

log = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task] = {}
_STOP = {"the", "and", "for", "with", "from", "about", "find", "what", "which", "where", "when", "how", "latest", "report", "statistics"}


def make_plan(objective: str, max_pages: int) -> ResearchPlan:
    topic = re.sub(r"\s+", " ", objective).strip()
    return ResearchPlan(
        topic=topic,
        required_fields=[],
        search_queries=[topic, f"{topic} official data", f"{topic} report findings"],
        max_pages=max_pages,
        stopping_criteria=[f"Read at most {max_pages} pages"],
    )


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.casefold()) if len(w) > 2 and w not in _STOP]


def _url_from_result(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com"):
        url = parse_qs(parsed.query).get("uddg", [""])[0]
        parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host or host == "localhost" or host.endswith(".local"):
        return ""
    try:
        if not ipaddress.ip_address(host).is_global:
            return ""
    except ValueError:
        pass
    if any(host == domain or host.endswith("." + domain) for domain in ("duckduckgo.com", "google.com", "bing.com")):
        return ""
    return parsed._replace(fragment="").geturl()


def _result_links(result, markdown: str) -> list[CandidateURL]:
    links = []
    seen = set()
    groups = getattr(result, "links", {}) or {}
    if isinstance(groups, dict):
        for link in [*groups.get("internal", []), *groups.get("external", [])]:
            if not isinstance(link, dict):
                continue
            url = _url_from_result(str(link.get("href") or ""))
            title = str(link.get("text") or link.get("title") or "").strip()
            if url and url not in seen and title:
                links.append(CandidateURL(url=url, title=title[:300], snippet=title, source="search"))
                seen.add(url)
    for title, raw_url in re.findall(r"\[([^\]]{3,240})\]\((https?://[^)\s]+)\)", markdown):
        url = _url_from_result(raw_url)
        if url and url not in seen:
            links.append(CandidateURL(url=url, title=title.strip()[:300], snippet=title.strip(), source="search"))
            seen.add(url)
    return links


async def discover(crawler: AsyncWebCrawler, plan: ResearchPlan) -> list[CandidateURL]:
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, word_count_threshold=5)
    best: list[CandidateURL] = []
    for query in plan.search_queries:
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            page = await crawler.arun(search_url, config=config)
            if not page.success:
                continue
            found = _result_links(page, str(page.markdown or ""))
            terms = set(_terms(plan.topic))
            found.sort(key=lambda item: sum(t in (item.title + " " + item.url).casefold() for t in terms), reverse=True)
            best.extend(found)
            # The first usable search page is enough; later terms are fallback queries.
            if found:
                break
        except Exception as exc:
            log.warning("Search-page crawl failed for %r: %s", query, exc)

    unique = {}
    for candidate in best:
        unique.setdefault(candidate.url, candidate)
    result = list(unique.values())[: max(plan.max_pages * 2, 10)]
    for index, candidate in enumerate(result):
        candidate.priority = round(max(.2, 1 - index * .04), 2)
    return result


def _relevant_text(markdown: str, objective: str) -> tuple[str, float]:
    lines = [re.sub(r"\s+", " ", line).strip(" #-*|") for line in markdown.splitlines()]
    lines = [line for line in lines if len(line) > 35 and not line.startswith(("[", "Cookie", "Subscribe", "Sign in"))]
    terms = _terms(objective)
    scored = [(sum(term in line.casefold() for term in terms), i, line) for i, line in enumerate(lines)]
    hits = [row for row in scored if row[0] > 0]
    if not hits:
        return "\n".join(lines[:25])[:12000], 0.05 if lines else 0.0
    selected = sorted(hits, key=lambda row: (-row[0], row[1]))[:45]
    text = "\n".join(row[2] for row in sorted(selected, key=lambda row: row[1]))[:16000]
    score = min(1.0, sum(row[0] for row in selected) / max(len(terms) * 8, 1))
    return text, score


def rank_records(records: list[ExtractedRecord], plan: ResearchPlan) -> list[RankedResult]:
    ranked = sorted(records, key=lambda r: (r.relevance_score, len(r.relevant_text)), reverse=True)
    return [RankedResult(
        rank=i,
        url=record.url,
        title=record.title,
        domain=record.domain,
        final_score=record.relevance_score,
        score_breakdown={"topic_match": record.relevance_score},
        score_explanation="Ranked by overlap with the research objective and useful extracted text.",
        evidence_snippets=record.evidence_snippets,
        dynamic_fields=record.dynamic_fields,
    ) for i, record in enumerate(ranked, 1)]


def _preview(candidate: CandidateURL, objective: str) -> ExtractedRecord | None:
    text = " ".join(p for p in (candidate.title, candidate.snippet) if p).strip()
    if not text:
        return None
    return ExtractedRecord(
        url=candidate.url,
        domain=urlparse(candidate.url).netloc,
        title=candidate.title or candidate.url,
        main_text=text,
        relevant_text=text,
        metadata={"retrieval_mode": "search_preview", "notice": "Only the search-result preview was readable."},
        relevance_score=max(.1, _relevant_text(text, objective)[1]),
        evidence_snippets=[EvidenceSnippet(text=text, source_url=candidate.url, confidence=.3)],
    )


async def run_task(task_id: str, objective: str, max_pages: int, crawl_depth: int) -> None:
    plan = make_plan(objective, max_pages)
    state = CrawlState(task_id=task_id, objective=objective, plan=plan, status=TaskStatus.RUNNING,
                       missing_fields=plan.required_fields)
    try:
        await database.save_plan(task_id, plan)
        await database.save_state(task_id, state)
        async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
            state.candidate_urls = await discover(crawler, plan)
            if not state.candidate_urls:
                state.stopping_reason = "Crawl4AI could not read search results for this objective."
            await database.save_state(task_id, state)
            config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, word_count_threshold=10, check_robots_txt=True)
            attempted, seen = 0, set()
            while state.candidate_urls and attempted < max_pages:
                candidate = state.candidate_urls.pop(0)
                if candidate.url in seen:
                    continue
                seen.add(candidate.url)
                attempted += 1
                state.visited_urls.append(candidate.url)
                state.progress_percent = min(95, 10 + 85 * attempted / max_pages)
                try:
                    page = await crawler.arun(candidate.url, config=config)
                    if not page.success:
                        state.failed_urls.append(candidate.url)
                        if preview := _preview(candidate, objective):
                            state.records.append(preview)
                        continue
                    state.pages_crawled += 1
                    markdown = str(page.markdown or "")
                    text, score = _relevant_text(markdown, objective)
                    if len(text) < 80:
                        if preview := _preview(candidate, objective):
                            state.records.append(preview)
                        continue
                    title = (page.metadata or {}).get("title") or candidate.title or candidate.url
                    record = ExtractedRecord(
                        url=page.url or candidate.url,
                        domain=urlparse(page.url or candidate.url).netloc,
                        title=str(title)[:300],
                        main_text=markdown[:50000],
                        relevant_text=text,
                        relevance_score=score,
                        evidence_snippets=[EvidenceSnippet(text=text[:500], source_url=candidate.url, confidence=.7)],
                    )
                    state.records.append(record)
                    if state.fallback_record is None or score > state.fallback_record.relevance_score:
                        state.fallback_record = record
                    if candidate.depth < crawl_depth:
                        topic = set(_terms(objective))
                        for link in _result_links(page, ""):
                            overlap = sum(word in (link.title + " " + link.url).casefold() for word in topic)
                            if overlap and link.url not in seen and all(x.url != link.url for x in state.candidate_urls):
                                link.depth = candidate.depth + 1
                                state.candidate_urls.append(link)
                                if len(state.candidate_urls) >= 20:
                                    break
                except Exception as exc:
                    log.warning("Could not crawl %s: %s", candidate.url, exc)
                    state.failed_urls.append(candidate.url)
                    if preview := _preview(candidate, objective):
                        state.records.append(preview)
                await database.save_state(task_id, state)

        state.records = list({r.url: r for r in state.records}.values())
        state.records.sort(key=lambda r: r.relevance_score, reverse=True)
        if state.records:
            state.fallback_record = state.fallback_record or state.records[0]
        else:
            state.stopping_reason = state.stopping_reason or "No readable source text was returned by Crawl4AI."
        state.status = TaskStatus.COMPLETED
        state.progress_percent = 100
        ranked = rank_records(state.records, plan)
        await database.save_report(task_id, await generate_report(state, ranked))
        await database.save_state(task_id, state)
        await database.update_task_status(task_id, state.status, 100, state.pages_crawled, state.stopping_reason)
    except asyncio.CancelledError:
        state.status = TaskStatus.CANCELLED
        state.stopping_reason = "Cancelled by user"
        await database.save_state(task_id, state)
        await database.update_task_status(task_id, state.status, state.progress_percent, state.pages_crawled, state.stopping_reason)
    except Exception as exc:
        log.exception("Research task failed")
        state.status = TaskStatus.FAILED
        state.stopping_reason = str(exc)[:200]
        await database.save_report(task_id, await generate_report(state, rank_records(state.records, plan)))
        await database.save_state(task_id, state)
        await database.update_task_status(task_id, state.status, state.progress_percent, state.pages_crawled, state.stopping_reason)


def start_research_task(task_id: str, objective: str, max_pages: int, crawl_depth: int) -> None:
    _tasks[task_id] = asyncio.create_task(run_task(task_id, objective, max_pages, crawl_depth))


async def cancel_research_task(task_id: str) -> None:
    task = _tasks.get(task_id)
    if task and not task.done():
        task.cancel()
