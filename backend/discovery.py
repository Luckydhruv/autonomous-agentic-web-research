"""Search discovery by asking the local Crawl4AI API to crawl search pages."""
from __future__ import annotations

import logging
import re
import urllib.parse

from crawl4ai_client import search
from schemas import CandidateURL, ResearchPlan

log = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc.lower(), path, "", parsed.query, "")
    )


def is_valid_http_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def search_result_matches_topic(
    url: str, title: str | None, snippet: str | None, plan: ResearchPlan,
) -> bool:
    """Drop obvious off-topic results before sending URLs to the crawler."""
    text = f"{url} {title or ''} {snippet or ''}".casefold()
    terms = [
        word for word in re.findall(r"[a-z0-9]+", plan.topic.casefold())
        if len(word) >= 3 and word not in {
            "the", "and", "for", "with", "from", "about", "statistics",
            "statistic", "data", "report", "latest", "research", "search",
        }
    ]
    if not terms:
        return True
    aliases = {"iiit": ("iiit", "iiitdm", "iiitdmj"), "iiitdm": ("iiitdm", "iiitdmj")}
    matched = [
        word for word in terms
        if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text)
               for alias in aliases.get(word, (word,)))
    ]
    if "placement" in plan.topic.casefold():
        entity_terms = [word for word in terms if word not in {"placement", "placements"}]
        entity_match = any(word in matched for word in entity_terms)
        placement_match = bool(re.search(
            r"\b(placements?|package|salary|salaries|ctc|recruiters?|report)\b", text,
        ))
        return entity_match and placement_match
    return bool(matched) and len(matched) / len(terms) >= 0.2


async def search_urls(query: str, max_results: int = 12) -> list[str]:
    """Return URLs parsed from a search-results page crawled by Crawl4AI."""
    return [row["url"] for row in await search(query, max_results)]


async def discover_candidate_urls(plan: ResearchPlan) -> list[CandidateURL]:
    """Search focused queries with Crawl4AI and return deduplicated candidates."""
    candidates: list[CandidateURL] = []
    fallback_rows: list[tuple[float, dict]] = []
    seen: set[str] = set()
    queries = plan.search_queries[: min(4, len(plan.search_queries))]
    for index, query in enumerate(queries):
        try:
            rows = await search(query, limit=10)
        except Exception:
            log.exception("Crawl4AI search failed for query %r", query)
            if index == 0:
                raise
            continue
        priority = 1.0 - (index / max(len(queries), 1)) * 0.25
        for row in rows:
            url = normalize_url(row["url"])
            if not is_valid_http_url(url):
                continue
            title = row.get("title") or ""
            snippet = row.get("snippet") or ""
            topic_terms = [
                token for token in re.findall(r"[a-z0-9]+", plan.topic.casefold())
                if len(token) >= 3 and token not in {"the", "and", "for", "with", "from", "about", "statistics", "statistic", "report"}
            ]
            evidence_text = f"{url} {title} {snippet}".casefold()
            overlap = sum(
                any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", evidence_text)
                    for alias in {token, *({"iiitdm", "iiitdmj", "iiit"} if token in {"iiit", "iiitdm", "iiitdmj"} else set())})
                for token in topic_terms
            ) / max(len(topic_terms), 1)
            fallback_rows.append((overlap + priority * 0.01, row))
            if url in seen:
                continue
            if not search_result_matches_topic(url, title, snippet, plan):
                continue
            seen.add(url)
            candidates.append(CandidateURL(
                url=url,
                priority=round(priority, 3),
                depth=0,
                source="crawl4ai_search",
                reason=f"Crawl4AI search: {query[:100]}",
                title=row.get("title") or None,
                snippet=row.get("snippet") or None,
            ))

    # Search engines sometimes return relevant sources whose title is too
    # abbreviated for the strict topic filter. Keep the best actual result as
    # a crawl seed rather than ending the research run with an empty queue.
    if not candidates and fallback_rows:
        _, row = max(fallback_rows, key=lambda item: item[0])
        url = normalize_url(row["url"])
        if is_valid_http_url(url):
            candidates.append(CandidateURL(
                url=url,
                priority=0.35,
                depth=0,
                source="crawl4ai_search_fallback",
                reason="Best available result from a Crawl4AI-crawled search page",
                title=row.get("title") or None,
                snippet=row.get("snippet") or None,
            ))
            log.warning("Strict topic matching returned no candidates; crawling the closest search result: %s", url)
    log.info("Crawl4AI discovered %d relevant URLs", len(candidates))
    return candidates


def score_link_relevance(url: str, anchor_text: str, plan: ResearchPlan) -> float:
    text = f"{url} {anchor_text}".casefold()
    words = [word for word in plan.topic.casefold().split() if len(word) >= 3]
    return sum(word in text for word in words) / max(len(words), 1)
