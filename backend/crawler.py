"""Adapter from the local Crawl4AI Python API to the research agent."""
from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass
from typing import Optional

from crawl4ai_client import Crawl4AIError, scrape

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    html: str
    content_type: str
    elapsed_ms: float
    error: Optional[str] = None
    links: list[tuple[str, str]] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status_code < 300


def _markdown_as_html(markdown: str, title: str) -> str:
    """Adapt Crawl4AI Markdown to the existing text extraction interface."""
    escaped = html.escape(markdown)
    body = "\n".join(
        f"<p>{line}</p>" for line in escaped.splitlines() if line.strip()
    )
    return (
        f"<html><head><title>{html.escape(title)}</title></head>"
        f"<body><main>{body}</main></body></html>"
    )


async def fetch_page(url: str) -> FetchResult:
    """Fetch and clean one page through the local Crawl4AI /crawl endpoint."""
    started = time.monotonic()
    try:
        payload = await scrape(url)
        markdown = payload["markdown"]
        metadata = payload.get("metadata") or payload.get("meta") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        title = str(
            payload.get("title")
            or metadata.get("title")
            or next(
                (line.lstrip("# ").strip() for line in markdown.splitlines()
                 if line.strip().startswith("#")),
                url,
            )
        )
        final_url = str(payload.get("url") or payload.get("final_url") or url)
        link_data = payload.get("links") or {}
        raw_links = link_data.get("internal", []) if isinstance(link_data, dict) else []
        links = [
            (str(link.get("href") or link.get("url")), str(link.get("text") or link.get("title") or ""))
            for link in raw_links
            if isinstance(link, dict) and (link.get("href") or link.get("url"))
        ] if isinstance(raw_links, list) else []
        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=int(payload.get("status") or payload.get("status_code") or 200),
            html=_markdown_as_html(markdown, title),
            content_type="text/markdown",
            elapsed_ms=(time.monotonic() - started) * 1000,
            links=links,
        )
    except Crawl4AIError as exc:
        log.warning("Crawl4AI could not fetch %s: %s", url, exc)
        return FetchResult(
            url=url,
            final_url=url,
            status_code=502,
            html="",
            content_type="",
            elapsed_ms=(time.monotonic() - started) * 1000,
            error=str(exc),
        )
    except Exception as exc:
        log.exception("Unexpected Crawl4AI fetch error for %s", url)
        return FetchResult(
            url=url,
            final_url=url,
            status_code=502,
            html="",
            content_type="",
            elapsed_ms=(time.monotonic() - started) * 1000,
            error=str(exc),
        )
