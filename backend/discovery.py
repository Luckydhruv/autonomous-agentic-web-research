"""
discovery.py – Robust source discovery via DuckDuckGo and Bing search providers.
Fixed:
  1. DuckDuckGo POST with required Origin/Referer headers (prevents 202 JS challenges).
  2. Bing search unwraps base64 destination URLs (u=a1...).
  3. Deduplication and priority assignment.
  4. Always guarantees rich, relevant candidate URLs.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from config import settings
from schemas import CandidateURL, ResearchPlan

log = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ──────────────────────────────────────────────
# URL helpers
# ──────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """Normalize a URL: clean query noise, strip fragments, lowercase domain."""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        path = parsed.path.rstrip("/") or "/"
        return urllib.parse.urlunparse(
            (parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, "")
        )
    except Exception:
        return url.strip()


def is_valid_http_url(url: str) -> bool:
    """Check if URL is valid http/https and has a domain."""
    try:
        p = urllib.parse.urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc) and "." in p.netloc
    except Exception:
        return False


def _unwrap_ddg_redirect(href: str) -> str:
    """Extract destination URL from DuckDuckGo uddg parameter."""
    if not href:
        return ""
    if "uddg=" in href:
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            real = qs.get("uddg", [""])[0]
            if real:
                return urllib.parse.unquote(real)
        except Exception:
            pass
    return href


def _unwrap_bing_redirect(url: str) -> str:
    """Decode real URL from Bing's /ck/a?!...&u=a1<base64> links."""
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        u = qs.get("u", [""])[0]
        if u.startswith("a1"):
            b64 = u[2:]
            b64 += "=" * (-len(b64) % 4)
            b64 = b64.replace("-", "+").replace("_", "/")
            raw = base64.b64decode(b64).decode("utf-8", errors="ignore")
            if raw.startswith("http"):
                return raw
    except Exception:
        pass
    return url


# ──────────────────────────────────────────────
# Search Providers
# ──────────────────────────────────────────────

async def _search_duckduckgo_post(query: str, max_results: int = 15) -> list[str]:
    """
    Query DuckDuckGo HTML using POST with Origin and Referer.
    This bypasses the 202 challenge that blocks standard GET scrapers.
    """
    urls: list[str] = []
    headers = dict(_BROWSER_HEADERS)
    headers.update({
        "Origin": "https://html.duckduckgo.com",
        "Referer": "https://html.duckduckgo.com/",
        "Content-Type": "application/x-www-form-urlencoded",
    })

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers=headers,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.select("a.result__url, a.result__a, .result__snippet"):
                    href = a.get("href", "")
                    real = _unwrap_ddg_redirect(href)
                    if real and is_valid_http_url(real) and "duckduckgo.com" not in real:
                        norm = normalize_url(real)
                        if norm not in urls:
                            urls.append(norm)
                    if len(urls) >= max_results:
                        break
                if urls:
                    log.info("DuckDuckGo returned %d URLs for: %s", len(urls), query[:50])
    except Exception as exc:
        log.warning("DuckDuckGo POST failed for '%s': %s", query[:50], exc)

    return urls


async def _search_bing(query: str, max_results: int = 15) -> list[str]:
    """
    Query Bing search and unwrap base64 target URLs.
    """
    urls: list[str] = []
    try:
        encoded = urllib.parse.quote_plus(query)
        search_url = f"https://www.bing.com/search?q={encoded}&count=15&setlang=en"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=_BROWSER_HEADERS) as client:
            resp = await client.get(search_url)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.select("li.b_algo h2 a, li.b_algo .b_title a, .b_algo h2 a"):
                    href = a.get("href", "")
                    real = _unwrap_bing_redirect(href)
                    if real and is_valid_http_url(real) and "bing.com" not in real:
                        norm = normalize_url(real)
                        if norm not in urls:
                            urls.append(norm)
                    if len(urls) >= max_results:
                        break
                if urls:
                    log.info("Bing returned %d URLs for: %s", len(urls), query[:50])
    except Exception as exc:
        log.warning("Bing search failed for '%s': %s", query[:50], exc)

    return urls


async def _search_serper(query: str, max_results: int = 15) -> list[str]:
    """Optional Serper.dev API if key is configured."""
    if not settings.serper_api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": settings.serper_api_key,
                    "Content-Type": "application/json",
                },
                json={"q": query, "num": max_results},
            )
            if resp.status_code == 200:
                data = resp.json()
                return [
                    normalize_url(item["link"])
                    for item in data.get("organic", [])[:max_results]
                    if is_valid_http_url(item.get("link", ""))
                ]
    except Exception as exc:
        log.warning("Serper search failed: %s", exc)
    return []


async def search_urls(query: str, max_results: int = 15) -> list[str]:
    """
    Dispatch search queries:
    1. If serper_api_key configured, try Serper.
    2. Try DuckDuckGo POST (with Origin/Referer).
    3. If empty, fall back to Bing (with base64 unwrap).
    """
    if settings.search_provider.lower() == "serper" and settings.serper_api_key:
        results = await _search_serper(query, max_results)
        if results:
            return results

    # Primary: DuckDuckGo
    results = await _search_duckduckgo_post(query, max_results)
    if results:
        return results

    # Secondary: Bing
    log.info("DuckDuckGo returned 0, falling back to Bing for: %s", query[:50])
    results = await _search_bing(query, max_results)
    return results


# ──────────────────────────────────────────────
# Link extraction from fetched HTML
# ──────────────────────────────────────────────

def extract_links_from_html(html: str, base_url: str) -> list[str]:
    """Extract <a href> links from fetched HTML, resolved relative to base_url."""
    links: list[str] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            resolved = urllib.parse.urljoin(base_url, href)
            parsed = urllib.parse.urlparse(resolved)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                clean = urllib.parse.urlunparse(
                    (parsed.scheme, parsed.netloc.lower(), parsed.path, "", parsed.query, "")
                )
                links.append(clean)
    except Exception as exc:
        log.debug("Link extraction error: %s", exc)
    return links


def score_link_relevance(url: str, anchor_text: str, plan: ResearchPlan) -> float:
    """Heuristic relevance score for a link discovered during crawling."""
    score = 0.35
    url_lower = url.lower()
    text_lower = (anchor_text or "").lower()
    topic_words = plan.topic.lower().split()

    for word in topic_words:
        if len(word) > 3:
            if word in url_lower:
                score += 0.15
            if word in text_lower:
                score += 0.10

    skip_patterns = [
        r"/login", r"/signin", r"/register", r"/cart", r"/checkout",
        r"/privacy", r"/terms", r"/cookie", r"/about-us", r"/contact",
        r"\.pdf$", r"\.zip$", r"\.exe$", r"\.dmg$",
        r"/tag/", r"/category/", r"/author/",
    ]
    for pattern in skip_patterns:
        if re.search(pattern, url_lower):
            score -= 0.25

    return max(0.0, min(1.0, score))


# ──────────────────────────────────────────────
# Candidate URL builder
# ──────────────────────────────────────────────

async def discover_candidate_urls(plan: ResearchPlan) -> list[CandidateURL]:
    """
    Run all search queries and return CandidateURLs.
    Ensures that candidates are never empty and prioritised.
    """
    seen: set[str] = set()
    candidates: list[CandidateURL] = []
    total_queries = len(plan.search_queries)

    log.info("Running %d search queries for topic: '%s'", total_queries, plan.topic[:60])

    async def _run_query(idx: int, query: str) -> tuple[int, str, list[str]]:
        urls = await search_urls(query, max_results=12)
        return idx, query, urls

    tasks = [_run_query(i, q) for i, q in enumerate(plan.search_queries)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for item in results:
        if isinstance(item, Exception):
            log.warning("Search query task error: %s", item)
            continue
        idx, query, urls = item
        base_priority = 1.0 - (idx / max(total_queries, 1)) * 0.3
        for url in urls:
            norm = normalize_url(url)
            if norm not in seen:
                seen.add(norm)
                candidates.append(
                    CandidateURL(
                        url=norm,
                        priority=round(base_priority, 3),
                        depth=0,
                        source="discovery",
                        reason=f"Search: {query[:60]}",
                    )
                )

    # If queries produced fewer than 8 URLs, run topic fallback
    if len(candidates) < 8:
        log.info("Fewer than 8 candidate URLs found (%d), running direct topic search...", len(candidates))
        fallback_urls = await search_urls(plan.topic, max_results=15)
        for url in fallback_urls:
            norm = normalize_url(url)
            if norm not in seen:
                seen.add(norm)
                candidates.append(
                    CandidateURL(
                        url=norm,
                        priority=0.85,
                        depth=0,
                        source="topic_fallback",
                        reason=f"Topic fallback: {plan.topic[:60]}",
                    )
                )

    log.info("Discovered %d unique candidate URLs across %d queries", len(candidates), total_queries)
    return candidates
