"""Crawl4AI search response filtering and candidate discovery."""
import pytest

from discovery import (
    discover_candidate_urls,
    is_valid_http_url,
    normalize_url,
    search_result_matches_topic,
)
from schemas import ResearchPlan


def _plan(topic: str = "IIIT Jabalpur placement statistics") -> ResearchPlan:
    return ResearchPlan(
        topic=topic,
        required_fields=["average_package", "highest_package"],
        search_queries=["IIIT Jabalpur official placement report"],
        max_pages=10,
    )


def test_normalize_url_removes_fragment_and_trailing_slash():
    assert normalize_url("https://EXAMPLE.com/page/#section") == "https://example.com/page"


def test_url_validation_allows_only_http_schemes():
    assert is_valid_http_url("https://example.com")
    assert is_valid_http_url("http://example.com")
    assert not is_valid_http_url("ftp://example.com")
    assert not is_valid_http_url("javascript:alert(1)")


def test_rejects_unrelated_results_for_placement_objective():
    plan = _plan()
    assert not search_result_matches_topic(
        "https://www.reddit.com/r/techsupport/", "Videos won't load", "", plan
    )


def test_accepts_institute_placement_results():
    plan = _plan()
    assert search_result_matches_topic(
        "https://iiitdmj.ac.in/placement",
        "IIITDM Jabalpur placement report",
        "Average package and placement rate",
        plan,
    )


@pytest.mark.asyncio
async def test_discovery_maps_crawl4ai_results(monkeypatch):
    async def fake_search(query, limit=10):
        return [
            {"url": "https://iiitdmj.ac.in/placement", "title": "IIITDM Jabalpur placements",
             "snippet": "Official placement statistics"},
            {"url": "https://reddit.com/r/random", "title": "Unrelated", "snippet": "unrelated"},
        ]

    monkeypatch.setattr("discovery.search", fake_search)
    candidates = await discover_candidate_urls(_plan())
    assert len(candidates) == 1
    assert candidates[0].url == "https://iiitdmj.ac.in/placement"
    assert candidates[0].source == "crawl4ai_search"
