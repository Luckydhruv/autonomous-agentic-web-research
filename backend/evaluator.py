"""
evaluator.py – Relevance scoring, coverage evaluation, duplicate detection,
conflict detection, and source reliability indicators.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone

from schemas import CrawlState, ExtractedRecord, ResearchPlan
from content_utils import extract_relevant_text

log = logging.getLogger(__name__)

# ── Source reliability heuristics ────────────────────────────────────────────
_HIGH_TRUST_DOMAINS = {
    "wikipedia.org", "reuters.com", "bbc.com", "nytimes.com",
    "theguardian.com", "nature.com", "sciencedirect.com", "pubmed.ncbi.nlm.nih.gov",
    "scholar.google.com", "arxiv.org", "who.int", "gov",
}

_LOW_TRUST_PATTERNS = [
    r"forum\.", r"\.reddit\.com", r"quora\.com",
    r"ask\.com", r"yahoo\.answers", r"4chan", r"\.blogspot\.",
]

_ECOMMERCE_DOMAINS = {
    "amazon", "flipkart", "ebay", "walmart", "bestbuy",
    "newegg", "bhphotovideo",
}


def source_reliability_score(domain: str) -> float:
    """Heuristic 0–1 score for how reliable a domain is."""
    d = domain.lower()

    # High trust
    for trusted in _HIGH_TRUST_DOMAINS:
        if trusted in d:
            return 0.9

    # E-commerce (moderate – real product data but potentially biased)
    for ecom in _ECOMMERCE_DOMAINS:
        if ecom in d:
            return 0.65

    # Low trust patterns
    for pattern in _LOW_TRUST_PATTERNS:
        if re.search(pattern, d):
            return 0.3

    # .edu / .gov bumps
    if d.endswith(".edu") or d.endswith(".gov"):
        return 0.85

    return 0.5   # generic unknown source


# ── Relevance scoring ─────────────────────────────────────────────────────────

def compute_relevance(record: ExtractedRecord, plan: ResearchPlan) -> float:
    """
    Relevance score 0–1 based on keyword overlap between page content
    and the research topic / constraints.
    """
    relevant_text = record.relevant_text or extract_relevant_text(
        record.main_text, plan.topic, record.title
    )
    text = (record.title + " " + relevant_text).lower()
    topic_words = re.findall(r"\w+", plan.topic.lower())
    constraint_words = []
    for c in plan.constraints:
        constraint_words.extend(re.findall(r"\w+", c.lower()))

    if not topic_words:
        return 0.5

    stop_words = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "about", "find", "search", "get", "give", "latest", "today", "please"}
    # Keep words >= 2 chars (preserves 'ai', 'ml')
    meaningful_topic = [w for w in topic_words if len(w) >= 2 and w not in stop_words]
    meaningful_constraints = [w for w in constraint_words if len(w) >= 2 and w not in stop_words]

    if not meaningful_topic:
        return 0.5

    # Whole-word matches prevent terms like "AI" matching "email" or "chair".
    topic_hits = 0
    for w in meaningful_topic:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text):
            topic_hits += 1
            if len(w) > 3 and len(re.findall(rf"(?<!\w){re.escape(w)}(?!\w)", text)) >= 3:
                topic_hits += 0.5

    topic_score = min(topic_hits / max(len(meaningful_topic), 1), 1.0)

    # Also check search queries for relevance
    query_boost = 0.0
    for query in plan.search_queries[:3]:
        q_words = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 3 and w not in stop_words]
        q_hits = sum(1 for w in q_words if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text))
        if q_words:
            query_boost = max(query_boost, q_hits / len(q_words) * 0.3)

    # Constraint keyword hits (bonus)
    constraint_hits = sum(1 for w in meaningful_constraints if w in text)
    constraint_bonus = min(constraint_hits / max(len(meaningful_constraints), 1), 0.3) if meaningful_constraints else 0.0

    final_score = min(topic_score + constraint_bonus + query_boost, 1.0)
    return round(final_score, 3)


# ── Field coverage ────────────────────────────────────────────────────────────

def compute_field_coverage(record: ExtractedRecord, plan: ResearchPlan) -> float:
    """Fraction of required fields that appear to be covered."""
    if not plan.required_fields:
        return 1.0
    covered = sum(
        1 for field in plan.required_fields
        if record.field_coverage.get(field, False)
    )
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
        r"([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        r"(\d{4})",
    ]
    for pat in date_patterns:
        m = re.search(pat, record.published_date)
        if m:
            try:
                if len(m.group(1)) == 4:
                    pub = datetime(int(m.group(1)), 1, 1, tzinfo=timezone.utc)
                elif "-" in m.group(1):
                    pub = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
                else:
                    pub = datetime.strptime(m.group(1).replace(",", ""), "%B %d %Y").replace(tzinfo=timezone.utc)
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
    values: dict[str, list[str]] = {}  # value -> [url, ...]
    for rec in records:
        dynamic_fields = rec.dynamic_fields if isinstance(rec.dynamic_fields, dict) else {}
        val = str(dynamic_fields.get(field, "")).strip()
        if val and val.lower() not in ("none", "null", ""):
            values.setdefault(val, []).append(rec.url)

    conflicts = []
    unique_values = list(values.keys())
    if len(unique_values) > 1:
        # Report the most common vs the minority
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
        # Re-score relevance
        rec.relevance_score = compute_relevance(rec, plan)
        # Update field coverage
        dynamic_fields = rec.dynamic_fields if isinstance(rec.dynamic_fields, dict) else {}
        rec.dynamic_fields = dynamic_fields
        rec.field_coverage = {
            f: bool(dynamic_fields.get(f))
            for f in plan.required_fields
        }

    # Missing fields: fields not covered by ANY record
    if plan.required_fields:
        state.missing_fields = [
            f for f in plan.required_fields
            if not any(
                isinstance(rec.field_coverage, dict) and rec.field_coverage.get(f, False)
                for rec in state.records
            )
        ]
    else:
        state.missing_fields = []

    # Conflict detection on key fields
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
    """
    plan = state.plan

    # Hard limit
    if state.pages_crawled >= plan.max_pages:
        return True, f"Reached maximum page budget ({plan.max_pages})"

    # Sufficient records with good coverage
    well_covered = [
        r for r in state.records
        if compute_field_coverage(r, plan) >= 0.5 and r.relevance_score >= 0.3
    ]
    if len(well_covered) >= min(10, plan.max_pages // 2):
        if not state.missing_fields:
            return True, "All required fields covered by sufficient sources"

    # No more candidates
    if not state.candidate_urls:
        return True, "No more candidate URLs to crawl"

    # Diminishing returns: last 5 records all low relevance
    if len(state.records) >= 5:
        recent_relevance = [r.relevance_score for r in state.records[-5:]]
        if all(s < 0.15 for s in recent_relevance):
            return True, "Diminishing returns: last 5 pages had very low relevance"

    return False, ""
