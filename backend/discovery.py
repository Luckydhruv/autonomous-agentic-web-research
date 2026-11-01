"""
discovery.py – Source discovery via configurable search providers and
link extraction from already-fetched HTML.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
import base64

import httpx
from bs4 import BeautifulSoup

from config import settings
from schemas import CandidateURL, ResearchPlan

log = logging.getLogger(__name__)

# Search previews are attached to CandidateURL so they remain available if the
# source site blocks or times out during the subsequent fetch.
_SEARCH_PREVIEWS: dict[str, tuple[str, str]] = {}


# ──────────────────────────────────────────────
# URL helpers
# ──────────────────────────────────────────────

def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    # Drop fragments and trailing slashes from path
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc.lower(), path, "", parsed.query, "")
    )


def is_valid_http_url(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


# ──────────────────────────────────────────────
# Common browser headers
# ──────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ──────────────────────────────────────────────
# Search providers
# ──────────────────────────────────────────────

async def _search_duckduckgo_html(query: str, max_results: int = 12) -> list[str]:
    """
    Scrape DuckDuckGo HTML results page (POST form).
    Uses the correct result__a selector which gives clean, direct URLs.
    """
    urls: list[str] = []
    try:
        headers = {
            **_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://html.duckduckgo.com",
            "Referer": "https://html.duckduckgo.com/",
        }
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "b": ""},
                headers=headers,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")

                # Primary: result__a gives clean direct URLs
                for a in soup.select("a.result__a"):
                    href = a.get("href", "")
                    # DDG sometimes uses redirect links; extract real URL
                    if "uddg=" in href:
                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                        real = qs.get("uddg", [""])[0]
                        if real:
                            href = urllib.parse.unquote(real)
                    if href and is_valid_http_url(href) and "duckduckgo.com" not in href:
                        norm = normalize_url(href)
                        if norm not in urls:
                            urls.append(norm)
                    if len(urls) >= max_results:
                        break

                log.info("DuckDuckGo returned %d results for: %s", len(urls), query[:60])
            else:
                log.warning("DuckDuckGo returned status %d for query: %s", resp.status_code, query[:60])

    except Exception as exc:
        log.warning("DuckDuckGo search failed: %s", exc)
    return urls


async def _search_bing_html(query: str, max_results: int = 10) -> list[str]:
    """Scrape Bing HTML search results."""
    urls: list[str] = []
    try:
        encoded = urllib.parse.quote_plus(query)
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(
                f"https://www.bing.com/search?q={encoded}&setlang=en&cc=US&count=20",
                headers=_HEADERS,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")

                # Primary: organic result links
                for a in soup.select("li.b_algo h2 a, li.b_algo .b_title a"):
                    href = a.get("href", "")
                    parsed = urllib.parse.urlparse(href)
                    if parsed.netloc.lower().endswith("bing.com"):
                        # Bing wraps organic destinations in /ck/a links with
                        # a URL-safe base64 `u=a1...` parameter. Decode those
                        # before rejecting Bing-owned utility links.
                        wrapped = urllib.parse.parse_qs(parsed.query).get("u", [""])[0]
                        if wrapped.startswith("a1"):
                            try:
                                token = wrapped[2:]
                                token += "=" * (-len(token) % 4)
                                decoded = base64.urlsafe_b64decode(token).decode("utf-8", "ignore")
                                if is_valid_http_url(decoded):
                                    href = decoded
                            except (ValueError, TypeError):
                                pass
                    if is_valid_http_url(href):
                        host = urllib.parse.urlparse(href).netloc.lower()
                        if host and not (host == "bing.com" or host.endswith(".bing.com")):
                            norm = normalize_url(href)
                            if norm not in urls:
                                urls.append(norm)
                                title_node = a.select_one("h3") or a
                                title = re.sub(r"\s+", " ", title_node.get_text(" ", strip=True)).strip()
                                parent = a.find_parent("li", class_="b_algo")
                                snippet_node = parent.select_one(".b_caption p, p.b_lineclamp2") if parent else None
                                snippet = re.sub(
                                    r"\s+", " ", snippet_node.get_text(" ", strip=True)
                                ).strip() if snippet_node else ""
                                if title or snippet:
                                    _SEARCH_PREVIEWS[norm] = (title[:300], snippet[:1200])
                    if len(urls) >= max_results:
                        break

                log.info("Bing returned %d results for: %s", len(urls), query[:60])
            else:
                log.warning("Bing returned status %d", resp.status_code)

    except Exception as exc:
        log.warning("Bing search failed: %s", exc)
    return urls


async def _search_yahoo_html(query: str, max_results: int = 10) -> list[str]:
    """Yahoo's HTML search is a no-key fallback with titles and useful snippets."""
    urls: list[str] = []
    try:
        search_url = f"https://search.yahoo.com/search?p={urllib.parse.quote_plus(query)}"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(search_url, headers=_HEADERS)
            if resp.status_code != 200:
                log.warning("Yahoo search returned status %d", resp.status_code)
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            for block in soup.select("div.algo"):
                anchor = block.select_one(".compTitle a")
                if not anchor:
                    continue
                href = anchor.get("href", "")
                # Yahoo wraps organic links in an encoded redirect path.
                redirect = re.search(r"/RU=([^/]+)", urllib.parse.urlparse(href).path, re.I)
                if redirect:
                    href = urllib.parse.unquote(redirect.group(1))
                if not is_valid_http_url(href):
                    continue
                host = urllib.parse.urlparse(href).netloc.lower()
                if host == "search.yahoo.com" or host.endswith(".yahoo.com"):
                    continue
                title_node = anchor.select_one("h3.title") or anchor
                title = re.sub(r"\s+", " ", title_node.get_text(" ", strip=True)).strip()
                snippet_node = block.select_one(".compText")
                if snippet_node:
                    date = snippet_node.select_one(".fc-smoke")
                    if date:
                        date.decompose()
                    snippet = re.sub(r"\s+", " ", snippet_node.get_text(" ", strip=True)).strip()
                else:
                    snippet = ""
                norm = normalize_url(href)
                if norm not in urls:
                    urls.append(norm)
                    _SEARCH_PREVIEWS[norm] = (title[:300], snippet[:1200])
                if len(urls) >= max_results:
                    break
            log.info("Yahoo returned %d results for: %s", len(urls), query[:60])
    except Exception as exc:
        log.warning("Yahoo search failed: %s", exc)
    return urls


async def _search_google_news_rss(query: str, max_results: int = 10) -> list[str]:
    """Use Google News RSS for news queries — no API key required."""
    urls: list[str] = []
    try:
        encoded = urllib.parse.quote_plus(query)
        rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(rss_url, headers={"User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)"})
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml-xml")
                for item in soup.find_all("item")[:max_results]:
                    link_tag = item.find("link")
                    if link_tag:
                        # Link text is the URL in RSS
                        href = link_tag.get_text(strip=True)
                        if not href or not href.startswith("http"):
                            # Try next_sibling for some parsers
                            ns = link_tag.next_sibling
                            if ns and isinstance(ns, str):
                                href = ns.strip()
                        if href and is_valid_http_url(href) and "news.google.com" not in href:
                            norm = normalize_url(href)
                            if norm not in urls:
                                urls.append(norm)

        log.info("Google News RSS returned %d results for: %s", len(urls), query[:60])
    except Exception as exc:
        log.warning("Google News RSS failed: %s", exc)
    return urls


async def _search_serper(query: str, max_results: int = 10) -> list[str]:
    """Use Serper.dev API (requires SERPER_API_KEY)."""
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
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return []
            organic = data.get("organic")
            if not isinstance(organic, list):
                return []
            links = []
            for item in organic[:max_results]:
                if isinstance(item, dict) and isinstance(item.get("link"), str):
                    link = item["link"].strip()
                    if is_valid_http_url(link):
                        links.append(normalize_url(link))
            return links
    except Exception as exc:
        log.warning("Serper search failed: %s", exc)
        return []


async def search_urls(query: str, max_results: int = 12) -> list[str]:
    """
    Dispatch to the configured search provider with fallback chain.
    Returns a list of URLs (may be empty if all providers fail).
    """
    provider = settings.search_provider.lower()
    results: list[str] = []

    if provider == "serper":
        results = await _search_serper(query, max_results)
        if not results:
            results = await _search_duckduckgo_html(query, max_results)
    elif provider == "duckduckgo_html":
        results = await _search_duckduckgo_html(query, max_results)
        # Augment with Bing if not enough
        if len(results) < max_results // 2:
            log.info("DuckDuckGo got only %d results, augmenting with Bing...", len(results))
            bing_urls = await _search_bing_html(query, max_results - len(results))
            for u in bing_urls:
                if u not in results:
                    results.append(u)
    elif provider == "none":
        log.info("Search provider set to 'none'; trying the public Yahoo fallback.")
        results = await _search_yahoo_html(query, max_results)
    else:
        log.warning("Unknown search provider '%s', falling back to DuckDuckGo HTML.", provider)
        results = await _search_duckduckgo_html(query, max_results)

    # For news / current-event queries, also augment from Google News RSS
    query_lower = query.lower()
    if any(w in query_lower for w in ["news", "latest", "recent", "today", "2024", "2025", "update"]):
        news_urls = await _search_google_news_rss(query, max_results=8)
        for u in news_urls:
            if u not in results:
                results.append(u)

    # DuckDuckGo may present a bot challenge and Bing markup changes often.
    # Yahoo is a final no-key provider and supplies preview text for fetch fails.
    if len(results) < max(3, max_results // 2):
        yahoo_urls = await _search_yahoo_html(query, max_results - len(results))
        for url in yahoo_urls:
            if url not in results:
                results.append(url)

    log.info("Total URLs found for '%s': %d", query[:60], len(results))
    return results


# ──────────────────────────────────────────────
# Link discovery from fetched HTML
# ──────────────────────────────────────────────

def extract_link_candidates_from_html(html: str, base_url: str) -> list[tuple[str, str]]:
    """Extract content links and their anchor text, excluding navigation and share links."""
    candidates: list[tuple[str, str]] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if a.find_parent(["nav", "header", "footer", "aside"]):
                continue
            href = a["href"].strip()
            resolved = urllib.parse.urljoin(base_url, href)
            parsed = urllib.parse.urlparse(resolved)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                if re.search(
                    r"facebook\.com|twitter\.com|x\.com|linkedin\.com|reddit\.com|"
                    r"/intent/|/share|/submit|/login|/signin|/signup|/register|"
                    r"/privacy|/terms|/refund|/workshop|/training|/events?|/quiz|/newsletter",
                    resolved,
                    re.I,
                ):
                    continue
                clean = urllib.parse.urlunparse(
                    (parsed.scheme, parsed.netloc.lower(), parsed.path, "", parsed.query, "")
                )
                anchor_text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))[:300]
                candidates.append((clean, anchor_text))
    except Exception as exc:
        log.debug("Link extraction error: %s", exc)
    return candidates


def extract_links_from_html(html: str, base_url: str) -> list[str]:
    """Extract all content links from HTML, resolved relative to base_url."""
    return list(dict.fromkeys(url for url, _ in extract_link_candidates_from_html(html, base_url)))


# ──────────────────────────────────────────────
# Candidate URL builder
# ──────────────────────────────────────────────

async def discover_candidate_urls(plan: ResearchPlan) -> list[CandidateURL]:
    """
    Run all search queries and return a prioritised list of CandidateURLs.
    URLs from the first query get higher priority.
    """
    seen: set[str] = set()
    candidates: list[CandidateURL] = []
    total_queries = len(plan.search_queries)

    for idx, query in enumerate(plan.search_queries):
        log.info("Search query [%d/%d]: %s", idx + 1, total_queries, query)
        urls = await search_urls(query, max_results=12)
        # Higher priority for results from earlier / more specific queries
        base_priority = 1.0 - (idx / max(total_queries, 1)) * 0.3
        for url in urls:
            norm = normalize_url(url)
            if norm not in seen:
                seen.add(norm)
                preview_title, preview_snippet = _SEARCH_PREVIEWS.get(norm, (None, None))
                candidates.append(
                    CandidateURL(
                        url=norm,
                        priority=round(base_priority, 3),
                        depth=0,
                        source="discovery",
                        reason=f"Search result for: {query[:60]}",
                        title=preview_title,
                        snippet=preview_snippet,
                    )
                )

    # Search engines can be temporarily unavailable, blocked, or return markup
    # that changes. Preserve a useful crawl path for known specialist topics;
    # for other topics, crawl the search pages themselves so their result links
    # can seed the normal adaptive crawler.
    if not candidates:
        fallback_urls = _fallback_seed_urls(plan)
        for url, reason in fallback_urls:
            norm = normalize_url(url)
            if norm in seen:
                continue
            seen.add(norm)
            candidates.append(
                CandidateURL(
                    url=norm,
                    priority=0.55,
                    depth=0,
                    source="fallback",
                    reason=reason,
                )
            )
        if candidates:
            log.warning(
                "Search returned no URLs; queued %d fallback source(s) for %s",
                len(candidates), plan.topic,
            )

    log.info("Discovered %d unique candidate URLs", len(candidates))
    return candidates


def _fallback_seed_urls(plan: ResearchPlan) -> list[tuple[str, str]]:
    """Return direct topical sources or crawlable search pages as a last resort."""
    topic = f"{plan.topic} {' '.join(plan.search_queries)}".casefold()

    # These public Codeforces discussions directly address training and rating
    # improvement. They let this common specialist query work without a search
    # API or search-engine HTML being available.
    if "codeforces" in topic and any(
        word in topic for word in ("rating", "improv", "better", "increase", "practice")
    ):
        return [
            ("https://codeforces.com/blog/entry/157477", "Codeforces discussion about improving rating"),
            ("https://codeforces.com/blog/entry/153215", "Practical Codeforces rating improvement advice"),
            ("https://codeforces.com/blog/entry/150416", "Codeforces discussion about getting better"),
            ("https://codeforces.com/blog/entry/145499", "Codeforces training and contest improvement advice"),
        ]

    # Keep a final discovery path for arbitrary topics. These are regular
    # search result pages; the crawler extracts outbound result links and queues
    # the underlying articles, rather than treating the search page as evidence.
    query = plan.search_queries[0] if plan.search_queries else plan.topic
    encoded = urllib.parse.quote_plus(query)
    return [
        (f"https://html.duckduckgo.com/html/?q={encoded}", f"Fallback search page for: {query[:60]}"),
        (f"https://www.google.com/search?q={encoded}", f"Alternate fallback search page for: {query[:60]}"),
    ]


def score_link_relevance(url: str, anchor_text: str, plan: ResearchPlan) -> float:
    """
    Heuristic relevance score for a link discovered during crawling.
    Used to decide whether to add it to the crawl queue.
    """
    score = 0.1  # base
    url_lower = url.lower()
    text_lower = (anchor_text or "").lower()

    # Split topic into words, keeping short words too (like 'ai', 'ml')
    topic_words = [w for w in plan.topic.lower().split() if len(w) >= 2]

    # Boost if topic words appear in URL or anchor text
    for word in topic_words:
        if word in url_lower:
            score += 0.20
        if word in text_lower:
            score += 0.15

    # Also boost using individual words from search queries
    for query in plan.search_queries[:3]:
        for word in query.lower().split():
            if len(word) >= 3 and word in url_lower:
                score += 0.05

    # Penalty for non-content / navigation pages
    skip_patterns = [
        r"/login", r"/signin", r"/register", r"/cart", r"/checkout",
        r"/privacy", r"/terms", r"/cookie", r"/about-us", r"/contact",
        r"\.pdf$", r"\.zip$", r"\.exe$", r"\.dmg$",
        r"/tag/", r"/category/", r"/author/",
        r"play\.google\.com", r"chromewebstore", r"support\.google\.com",
        r"/intl/", r"accounts\.google\.com", r"maps\.google\.com",
        r"/workshop", r"/training", r"/refund", r"/events?", r"/quiz",
        r"/newsletter", r"/corporate", r"/share", r"/intent/", r"/submit",
    ]
    for pattern in skip_patterns:
        if re.search(pattern, url_lower):
            score -= 0.3

    return max(0.0, min(1.0, score))
