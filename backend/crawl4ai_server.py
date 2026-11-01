"""Minimal local REST wrapper around Crawl4AI's Python crawler (no Docker)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

API_TOKEN = os.getenv("CRAWL4AI_API_TOKEN", "").strip()
crawler: AsyncWebCrawler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global crawler
    crawler = AsyncWebCrawler(config=BrowserConfig(headless=True))
    await crawler.start()
    yield
    if crawler is not None:
        await crawler.close()
        crawler = None


app = FastAPI(title="Local Crawl4AI API", version="1.0.0", lifespan=lifespan)


class CrawlRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)


def require_token(authorization: str | None) -> None:
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="CRAWL4AI_API_TOKEN is not configured")
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/crawl")
async def crawl(request: CrawlRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_token(authorization)
    if crawler is None:
        raise HTTPException(status_code=503, detail="Crawl4AI is still starting")

    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=10,
        check_robots_txt=True,
    )
    results = await crawler.arun_many(urls=request.urls, config=config)
    rows: list[dict[str, Any]] = []
    for result in results:
        markdown = result.markdown
        if not isinstance(markdown, str):
            markdown = getattr(markdown, "fit_markdown", "") or getattr(markdown, "raw_markdown", "")
        # Crawl4AI returns a str subclass with extra attributes; cast to a
        # plain str so FastAPI does not serialize it as an empty object.
        markdown = str(markdown or "")
        rows.append({
            "success": bool(result.success),
            "url": result.url,
            "title": (result.metadata or {}).get("title", ""),
            "markdown": markdown,
            "metadata": result.metadata or {},
            "links": result.links or {},
            "status_code": result.status_code or 200,
            "error_message": result.error_message or "",
        })
    return {"results": rows}
