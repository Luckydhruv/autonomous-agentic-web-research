"""
evaluator.py – Relevance scoring, coverage evaluation, duplicate detection,
conflict detection, and source reliability indicators.
Improved: semantic relevance scoring, better thresholds, min-pages enforcement.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone

from schemas import CrawlState, ExtractedRecord, ResearchPlan

log = logging.getLogger(__name__)

# Minimum pages to crawl before any early-stopping
MIN_PAGES_BEFORE_STOP = 10

# ── Source reliability heuristics ────────────────────────────────────────────
_HIGH_TRUST_DOMAINS = {
    "wikipedia.org", "reuters.com", "bbc.com", "nytimes.com",
    "theguardian.com", "nature.com", "sciencedirect.com", "pubmed.ncbi.nlm.nih.gov",
    "scholar.google.com", "arxiv.org", "who.int", "gov",
    "techcrunch.com", "wired.com", "arstechnica.com", "theverge.com",
    "mit.edu", "stanford.edu", "cnn.com", "bbc.co.uk", "apnews.com",
    "bloomberg.com", "forbes.com", "wsj.com", "economist.com",
    "openai.com", "deepmind.com", "microsoft.com", "google.com",
}

_LOW_TRUST_PATTERNS = [
    r"forum\.", r"\.reddit\.com", r"quora\.com",
    r"ask\.com", r"yahoo\.answers", r"4chan", r"\.blogspot\.",
    r"clickbait", r"buzzfeed\.com",
]

_ECOMMERCE_DOMAINS = {
    "amazon", "flipkart", "ebay", "walmart", "bestbuy",
    "newegg", "bhphotovideo", "croma", "reliance",
}


def source_reliability_score(domain: str) -> float:
    """Heuristic 0–1 score for how reliable a domain is."""
    d = domain.lower()

    for trusted in _HIGH_TRUST_DOMAINS:
        if trusted in d:
            return 0.9

    for ecom in _ECOMMERCE_DOMAINS:
        if ecom in d:
            return 0.65

    for pattern in _LOW_TRUST_PATTERNS:
        if re.search(pattern, d):
            return 0.3

    if d.endswith(".edu") or d.endswith(".gov"):
        return 0.85

    return 0.5


# ── Relevance scoring ─────────────────────────────────────────────────────────

def compute_relevance(record: ExtractedRecord, plan: ResearchPlan) -> float:
    """
    Relevance score 0–1 based on:
    1. Topic keyword overlap (weighted by position — title hits count more)
    2. Constraint keyword overlap (bonus)
    3. Context: partial-word and phrase matching
    """
    title_text = record.title.lower()
    body_text = record.main_text.lower()
    full_text = title_text + " " + body_text

    # Tokenise topic into meaningful words (>2 chars)
    topic_words = [w for w in re.findall(r"\w+", plan.topic.lower()) if len(w) > 2]
    if not topic_words:
        return 0.5

    # Also extract multi-word phrases from the topic
    topic_lower = plan.topic.lower()

    title_score = 0.0
    body_score = 0.0

    for word in topic_words:
        if word in title_text:
            title_score += 1.0
        elif word in body_text:
            body_score += 0.5

    # Bonus for exact topic phrase in text
    if topic_lower in full_text:
        title_score += 2.0
    elif topic_lower in body_text:
        body_score += 1.0

    max_possible = len(topic_words) + 2.0  # +2 for phrase bonus
    raw_topic_score = (title_score + body_score) / max(max_possible, 1)
    topic_score = min(raw_topic_score, 1.0)

    # Constraint keyword hits (bonus up to 0.2)
    constraint_words = []
    for c in plan.constraints:
        constraint_words.extend([w for w in re.findall(r"\w+", c.lower()) if len(w) > 3])

    constraint_bonus = 0.0
    if constraint_words:
        constraint_hits = sum(1 for w in constraint_words if w in full_text)
        constraint_bonus = min(constraint_hits / max(len(constraint_words), 1), 0.2)

    return round(min(topic_score + constraint_bonus, 1.0), 3)


# ── Field coverage ────────────────────────────────────────────────────────────

def compute_field_coverage(record: ExtractedRecord, plan: ResearchPlan) -> float:
    """Fraction of required fields that appear to be covered."""
    if not plan.required_fields:
        return 1.0
    covered = sum(1 for v in record.field_coverage.values() if v)
    return round(covered / len(plan.required_fields), 3)


# ── Freshness scoring ─────────────────────────────────────────────────────────

def compute_freshness(record: ExtractedRecord) -> float:
    """
    Freshness score 0–1 based on published_date if available.
    Penalises pages older than 2 years.
    """
    if not record.published_date:
        return 0.5  # Unknown date – neutral

    now = datetime.now(timezone.utc)
    date_patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4})",
    ]
    for pat in date_patterns:
        m = re.search(pat, record.published_date)
        if m:
            try:
                if len(m.group(1)) == 4:
                    pub = datetime(int(m.group(1)), 1, 1, tzinfo=timezone.utc)
                else:
                    pub = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
                age_days = (now - pub).days
                if age_days < 30:
                    return 1.0
                if age_days < 365:
                    return 0.8
                if age_days < 730:
                    return 0.6
                return max(0.2, 1.0 - age_days / 3650)
            except (ValueError, OverflowError):
                pass
    return 0.5


# ── Duplicate detection ───────────────────────────────────────────────────────

def _content_hash(record: ExtractedRecord) -> str:
    """SHA-256 of first 500 chars of main text – quick similarity proxy."""
    snippet = record.main_text[:500].strip().lower()
    return hashlib.sha256(snippet.encode()).hexdigest()


def is_duplicate(
    record: ExtractedRecord,
    existing_records: list[ExtractedRecord],
) -> bool:
    """
    True if the record's content hash matches an existing record,
    or if the URL (ignoring trailing slash) already exists.
    """
    new_hash = _content_hash(record)
    new_url_clean = record.url.rstrip("/").lower()

    for existing in existing_records:
        if existing.url.rstrip("/").lower() == new_url_clean:
            return True
        if _content_hash(existing) == new_hash:
            return True
    return False


# ── Conflict detection ────────────────────────────────────────────────────────

def detect_conflicts(records: list[ExtractedRecord], field: str) -> list[str]:
    """
    Find records that have conflicting values for a given field.
    Returns a list of conflict description strings.
    """
    values: dict[str, list[str]] = {}
    for rec in records:
        val = str(rec.dynamic_fields.get(field, "")).strip()
        if val and val.lower() not in ("none", "null", ""):
            values.setdefault(val, []).append(rec.url)

    conflicts = []
    unique_values = list(values.keys())
    if len(unique_values) > 1:
        conflicts.append(
            f"Field '{field}' has {len(unique_values)} different values: "
            + ", ".join(f"'{v}' ({len(urls)} sources)" for v, urls in values.items())
        )
    return conflicts


# ── State-level evaluation ────────────────────────────────────────────────────

def evaluate_state(state: CrawlState) -> CrawlState:
    """
    Run all evaluations on the current crawl state:
    - Update relevance scores on all records
    - Detect missing required fields
    - Detect conflicts across records
    Returns updated state.
    """
    plan = state.plan

    for rec in state.records:
        rec.relevance_score = compute_relevance(rec, plan)
        rec.field_coverage = {
            f: (f in rec.dynamic_fields or f.lower() in rec.main_text.lower())
            for f in plan.required_fields
        }

    if plan.required_fields:
        state.missing_fields = [
            f for f in plan.required_fields
            if not any(rec.field_coverage.get(f, False) for rec in state.records)
        ]
    else:
        state.missing_fields = []

    conflict_list: list[str] = []
    for f in ["price", "rating", "specifications"]:
        if f in plan.required_fields:
            conflicts = detect_conflicts(state.records, f)
            conflict_list.extend(conflicts)
    state.conflicting_fields = conflict_list

    return state


# ── Stopping criteria ─────────────────────────────────────────────────────────

def should_stop(state: CrawlState) -> tuple[bool, str]:
    """
    Decide whether the research agent should stop.
    Returns (stop_bool, reason_string).
    Never stops before MIN_PAGES_BEFORE_STOP pages unless there are truly no more URLs.
    """
    plan = state.plan

    # Hard limit
    if state.pages_crawled >= plan.max_pages:
        return True, f"Reached maximum page budget ({plan.max_pages})"

    # Never stop early before the minimum
    if state.pages_crawled < MIN_PAGES_BEFORE_STOP:
        if not state.candidate_urls:
            return True, f"No more candidate URLs to crawl (only {state.pages_crawled} pages crawled)"
        return False, ""

    # No more candidates
    if not state.candidate_urls:
        return True, "No more candidate URLs to crawl"

    # Sufficient records with good coverage
    well_covered = [
        r for r in state.records
        if compute_field_coverage(r, plan) >= 0.4 and r.relevance_score >= 0.25
    ]
    if len(well_covered) >= min(15, plan.max_pages // 2):
        if not state.missing_fields:
            return True, "All required fields covered by sufficient sources"

    # Diminishing returns: last 7 records all very low relevance
    if len(state.records) >= 7:
        recent_relevance = [r.relevance_score for r in state.records[-7:]]
        if all(s < 0.1 for s in recent_relevance):
            return True, "Diminishing returns: last 7 pages had very low relevance"

    return False, ""
