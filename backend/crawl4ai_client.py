"""Small async client for the self-hosted Crawl4AI Docker API."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx

from config import settings

class Crawl4AIError(RuntimeError):
    """Raised when Crawl4AI is not configured or rejects a request."""


def _headers() -> dict[str, str]:
    if not settings.crawl4ai_api_token.strip():
        raise Crawl4AIError(
            "Crawl4AI API token is missing. Set CRAWL4AI_API_TOKEN in your .env file."
        )
    return {
        "Authorization": f"Bearer {settings.crawl4ai_api_token.strip()}",
        "Accept": "application/json",
    }


async def check_service() -> bool:
    """Check that the local Crawl4AI container is reachable before a task starts."""
    base = settings.crawl4ai_api_base.rstrip("/") + "/"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(urljoin(base, "health"))
            response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        raise Crawl4AIError(
            f"Local Crawl4AI is unavailable at {settings.crawl4ai_api_base}. "
            "Start it with start_crawl4ai.sh (or start_crawl4ai.bat)."
        ) from exc


async def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Crawl a search-results page with Crawl4AI and parse its result links.

    The self-hosted Crawl4AI API intentionally exposes crawling, not a search
    engine. Search result pages are therefore fetched by Crawl4AI itself.
    """
    base = settings.crawl4ai_api_base.rstrip("/") + "/"
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query[:512])}"
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            urljoin(base, "crawl"),
            json=_crawl_payload([search_url]),
            headers={**_headers(), "Content-Type": "application/json"},
        )
        if response.is_error:
            detail = response.text[:400]
            raise Crawl4AIError(f"Crawl4AI search-page crawl failed ({response.status_code}): {detail}")
        payload = response.json()

    result = _first_result(payload)
    if not result or result.get("success") is False:
        reason = (result or {}).get("error_message") or payload.get("error") or "No search page result"
        raise Crawl4AIError(str(reason))

    markdown = _markdown_text(result.get("markdown"))
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for title, url in re.findall(r"\[([^\]]{3,240})\]\((https?://[^)\s]+)\)", markdown):
        if "duckduckgo.com" in url or url in seen:
            continue
        seen.add(url)
        parsed.append({
            "url": url,
            "title": title.strip(),
            "snippet": _nearby_markdown_text(markdown, title),
            "provider": "crawl4ai_crawled_search_page",
        })
        if len(parsed) >= limit:
            break
    return parsed


async def scrape(url: str) -> dict[str, Any]:
    """Fetch one public page as cleaned Markdown through Crawl4AI."""
    base = settings.crawl4ai_api_base.rstrip("/") + "/"
    async with httpx.AsyncClient(timeout=125) as client:
        response = await client.post(
            urljoin(base, "crawl"),
            json=_crawl_payload([url]),
            headers={**_headers(), "Content-Type": "application/json"},
        )
        if response.is_error:
            detail = response.text[:400]
            raise Crawl4AIError(
                f"Crawl4AI scrape failed ({response.status_code}): {detail}"
            )
        payload = response.json()
    if not isinstance(payload, dict):
        raise Crawl4AIError("Crawl4AI returned an unexpected scrape response.")
    result = _first_result(payload)
    if not result or result.get("success") is False:
        raise Crawl4AIError(str((result or {}).get("error_message") or "Crawl4AI could not read this page."))
    markdown = _markdown_text(result.get("markdown"))
    if not markdown:
        raise Crawl4AIError("Crawl4AI returned no readable Markdown for this page.")
    result["markdown"] = markdown
    return result


def _crawl_payload(urls: list[str]) -> dict[str, Any]:
    return {
        "urls": urls,
        "browser_config": {
            "type": "BrowserConfig",
            "params": {"headless": True},
        },
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": {
                "stream": False,
                "cache_mode": "bypass",
                "word_count_threshold": 10,
                "check_robots_txt": True,
            },
        },
    }


def _first_result(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if isinstance(results, list) and results and isinstance(results[0], dict):
        return results[0]
    if isinstance(payload.get("result"), dict):
        return payload["result"]
    return None


def _markdown_text(value: Any) -> str:
    """Normalize both string and structured Crawl4AI Markdown responses."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("fit_markdown", "raw_markdown", "markdown"):
            content = value.get(key)
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _nearby_markdown_text(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if title.casefold() in line.casefold():
            return " ".join(part.strip() for part in lines[index:index + 3] if part.strip())[:1000]
    return ""
