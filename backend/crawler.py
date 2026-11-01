"""
crawler.py – HTTP page fetcher with SSRF protection, rate limiting,
robots.txt checking, and configurable limits.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional
from urllib.robotparser import RobotFileParser

import httpx

from config import settings

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Private IP ranges to block (SSRF protection)
_PRIVATE_RANGES = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


@dataclass
class FetchResult:
    url: str
    final_url: str          # after redirects
    status_code: int
    html: str
    content_type: str
    elapsed_ms: float
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status_code < 300


# ──────────────────────────────────────────────
# SSRF guard
# ──────────────────────────────────────────────

def _is_safe_url(url: str) -> tuple[bool, str]:
    """
    Returns (is_safe, reason).
    Blocks private/loopback IPs and configured blocked domains.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"Scheme '{parsed.scheme}' not allowed"

        hostname = parsed.hostname or ""

        # Check domain blocklist
        for blocked in settings.blocked_domains:
            if hostname == blocked or hostname.endswith(f".{blocked}"):
                return False, f"Domain '{hostname}' is blocked"

        # Resolve and check IP ranges
        try:
            addr = socket.getaddrinfo(hostname, None)[0][4][0]
            ip = ipaddress.ip_address(addr)
            for net in _PRIVATE_RANGES:
                if ip in net:
                    return False, f"IP {addr} is in a private/reserved range"
        except socket.gaierror:
            pass  # DNS failure – let the HTTP client handle it

        return True, ""
    except Exception as exc:
        return False, str(exc)


# ──────────────────────────────────────────────
# Rate limiter (per domain)
# ──────────────────────────────────────────────

class _RateLimiter:
    def __init__(self, delay: float):
        self._delay = delay
        self._last_hit: dict[str, float] = {}

    async def wait(self, domain: str) -> None:
        now = time.monotonic()
        last = self._last_hit.get(domain, 0.0)
        wait_time = self._delay - (now - last)
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        self._last_hit[domain] = time.monotonic()


_rate_limiter = _RateLimiter(settings.default_rate_limit_delay)


# ──────────────────────────────────────────────
# robots.txt cache
# ──────────────────────────────────────────────

_robots_cache: dict[str, RobotFileParser] = {}


async def _get_robots(base_url: str) -> RobotFileParser:
    parsed = urllib.parse.urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in _robots_cache:
        return _robots_cache[origin]
    rp = RobotFileParser()
    robots_url = f"{origin}/robots.txt"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(robots_url)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
    except Exception:
        pass  # If robots.txt is unavailable, assume allowed
    _robots_cache[origin] = rp
    return rp


async def _is_allowed_by_robots(url: str) -> bool:
    try:
        rp = await _get_robots(url)
        return rp.can_fetch("*", url)
    except Exception:
        return True  # Fail open


# ──────────────────────────────────────────────
# Page fetcher
# ──────────────────────────────────────────────

async def fetch_page(url: str) -> FetchResult:
    """
    Fetch a page with safety checks, rate limiting, and size limits.
    Returns FetchResult – callers should check .ok before using .html.
    """
    # SSRF check
    safe, reason = _is_safe_url(url)
    if not safe:
        log.warning("Blocked URL (SSRF): %s – %s", url, reason)
        return FetchResult(
            url=url, final_url=url, status_code=0, html="",
            content_type="", elapsed_ms=0, error=f"Blocked: {reason}"
        )

    # robots.txt check
    if not await _is_allowed_by_robots(url):
        log.info("Blocked by robots.txt: %s", url)
        return FetchResult(
            url=url, final_url=url, status_code=0, html="",
            content_type="", elapsed_ms=0, error="Blocked by robots.txt"
        )

    # Rate limit
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc
    await _rate_limiter.wait(domain)

    start = time.monotonic()
    try:
        max_bytes = settings.default_max_response_size_kb * 1024
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        async with httpx.AsyncClient(
            timeout=settings.default_request_timeout,
            follow_redirects=True,
            max_redirects=5,
            headers=headers,
        ) as client:
            resp = await client.get(url)
            elapsed = (time.monotonic() - start) * 1000

            content_type = resp.headers.get("content-type", "")
            # Only process HTML / text
            if "html" not in content_type and "text" not in content_type:
                return FetchResult(
                    url=url, final_url=str(resp.url),
                    status_code=resp.status_code, html="",
                    content_type=content_type, elapsed_ms=elapsed,
                    error=f"Non-HTML content-type: {content_type}"
                )

            # Enforce size limit
            html = resp.text[:max_bytes]

            # SSRF redirect check – make sure final URL is also safe
            final_url = str(resp.url)
            if final_url != url:
                safe2, reason2 = _is_safe_url(final_url)
                if not safe2:
                    return FetchResult(
                        url=url, final_url=final_url,
                        status_code=resp.status_code, html="",
                        content_type=content_type, elapsed_ms=elapsed,
                        error=f"Redirect blocked: {reason2}"
                    )

            return FetchResult(
                url=url, final_url=final_url,
                status_code=resp.status_code, html=html,
                content_type=content_type, elapsed_ms=elapsed,
            )

    except httpx.TimeoutException:
        elapsed = (time.monotonic() - start) * 1000
        return FetchResult(
            url=url, final_url=url, status_code=0, html="",
            content_type="", elapsed_ms=elapsed, error="Request timed out"
        )
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        log.debug("Fetch error for %s: %s", url, exc)
        return FetchResult(
            url=url, final_url=url, status_code=0, html="",
            content_type="", elapsed_ms=elapsed, error=str(exc)
        )
