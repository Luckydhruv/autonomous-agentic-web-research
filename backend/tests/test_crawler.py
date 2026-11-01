"""Crawl4AI scraper adapter tests."""
import pytest

from crawler import _markdown_as_html, fetch_page


def test_markdown_adapter_escapes_untrusted_markup():
    result = _markdown_as_html("# Heading\n\n<script>alert(1)</script>", "Page")
    assert "<title>Page</title>" in result
    assert "&lt;script&gt;" in result
    assert "<script>" not in result


@pytest.mark.asyncio
async def test_fetch_page_maps_crawl4ai_result(monkeypatch):
    async def fake_scrape(url):
        return {
            "url": url,
            "title": "Example title",
            "markdown": "# Example title\n\nReadable page body.",
            "links": {"internal": [{"href": "https://example.com/next", "text": "Next"}]},
        }

    monkeypatch.setattr("crawler.scrape", fake_scrape)
    result = await fetch_page("https://example.com")
    assert result.ok
    assert "Readable page body." in result.html
    assert result.links == [("https://example.com/next", "Next")]


@pytest.mark.asyncio
async def test_fetch_page_returns_provider_error(monkeypatch):
    async def fail(url):
        from crawl4ai_client import Crawl4AIError
        raise Crawl4AIError("API token missing")

    monkeypatch.setattr("crawler.scrape", fail)
    result = await fetch_page("https://example.com")
    assert not result.ok
    assert "API token missing" in result.error
