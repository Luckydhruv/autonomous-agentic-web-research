"""
planner.py – Convert a natural-language research objective into a
validated ResearchPlan.  Uses LLM when available; falls back to a
deterministic keyword-based planner.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from config import settings
from llm_client import generate_json
from schemas import ResearchPlan

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# LLM-based planner
# ──────────────────────────────────────────────

_PLAN_PROMPT = """You are a research planning agent. Convert the following research objective into a structured JSON plan.

Research Objective: {objective}
Max Pages Budget: {max_pages}

Return ONLY valid JSON with this exact structure (no explanation, no markdown):
{{
  "topic": "concise topic name",
  "constraints": ["list of constraints like price, location, time range"],
  "required_fields": ["list of fields to extract, e.g. name, price, rating"],
  "search_queries": ["3-5 specific search queries to find relevant pages"],
  "source_categories": ["types of sites to look for, e.g. review sites, e-commerce"],
  "ranking_priorities": ["what matters most for ranking, e.g. price, rating, freshness"],
  "max_pages": {max_pages},
  "stopping_criteria": ["when to consider research complete"],
  "extraction_schema": {{
    "field_name": "description of what to extract",
    "field_name2": "description"
  }}
}}"""


async def plan_with_llm(objective: str, max_pages: int) -> ResearchPlan | None:
    prompt = _PLAN_PROMPT.format(objective=objective, max_pages=max_pages)
    data = await generate_json(prompt)
    if not data:
        return None
    try:
        # Ensure max_pages is set from the user input, not the LLM
        data["max_pages"] = max_pages
        return ResearchPlan.model_validate(data)
    except Exception as exc:
        log.warning("LLM plan validation failed: %s", exc)
        return None


# ──────────────────────────────────────────────
# Deterministic fallback planner
# ──────────────────────────────────────────────

def _extract_price_constraint(text: str) -> list[str]:
    constraints = []
    price_pattern = re.compile(
        r"(under|below|less than|up to|within|max(?:imum)?)\s*"
        r"[\$₹€£]?\s*[\d,]+",
        re.IGNORECASE,
    )
    for m in price_pattern.finditer(text):
        constraints.append(m.group(0).strip())
    return constraints


def _extract_location(text: str) -> list[str]:
    location_keywords = [
        r"\bin\s+(india|usa|uk|us|canada|australia|germany|france|japan)\b",
        r"\bin\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*",
    ]
    for pattern in location_keywords:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return [m.group(0).strip()]
    return []


def _infer_fields(objective: str) -> list[str]:
    obj_lower = objective.lower()
    field_map = {
        ("laptop", "computer", "pc", "notebook"): [
            "name", "price", "cpu", "gpu", "ram", "storage", "display", "rating", "availability"
        ],
        ("phone", "smartphone", "mobile"): [
            "name", "price", "processor", "camera", "battery", "display", "rating"
        ],
        ("hotel", "resort", "accommodation"): [
            "name", "price_per_night", "location", "rating", "amenities", "availability"
        ],
        ("job", "career", "hiring", "vacancy"): [
            "title", "company", "location", "salary", "experience_required", "posted_date"
        ],
        ("movie", "film", "show", "series"): [
            "title", "genre", "rating", "director", "cast", "release_year", "synopsis"
        ],
        ("research", "paper", "study", "article"): [
            "title", "authors", "publication", "year", "abstract", "doi"
        ],
        ("recipe", "food", "dish", "cook"): [
            "name", "ingredients", "prep_time", "cook_time", "servings", "calories"
        ],
        ("ai", "artificial intelligence", "machine learning", "ml", "llm", "gpt", "chatgpt", "openai"):
            ["title", "summary", "date", "source", "impact", "category"],
        ("news", "latest", "recent", "today", "breaking"):
            ["title", "summary", "date", "source", "url", "category"],
        ("stock", "market", "finance", "investment", "crypto"):
            ["name", "price", "change", "volume", "market_cap", "date"],
        ("health", "medical", "disease", "treatment", "drug"):
            ["title", "summary", "source", "date", "evidence_level"],
    }
    for keywords, fields in field_map.items():
        if any(kw in obj_lower for kw in keywords):
            return fields
    # Generic fallback
    return ["title", "summary", "date", "source", "url"]


def _infer_source_categories(objective: str) -> list[str]:
    obj_lower = objective.lower()
    if any(w in obj_lower for w in ["buy", "price", "purchase", "shop", "under", "cheap"]):
        return ["e-commerce", "price comparison", "review sites"]
    if any(w in obj_lower for w in ["review", "rating", "best", "top", "recommend"]):
        return ["review aggregators", "tech blogs", "consumer reports"]
    if any(w in obj_lower for w in ["research", "study", "paper", "academic"]):
        return ["academic databases", "journals", "preprint servers"]
    if any(w in obj_lower for w in ["news", "latest", "recent", "today"]):
        return ["news sites", "blogs", "RSS feeds"]
    return ["general web", "informational sites", "directories"]


def _generate_search_queries(objective: str, topic: str) -> list[str]:
    """Generate diverse, specific search queries for better coverage."""
    # Strip common filler words for cleaner queries
    clean = re.sub(r"\b(please|find|me|the|a|an|some)\b", "", objective, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip()

    obj_lower = objective.lower()
    queries = [clean]

    # News / current events queries
    if any(w in obj_lower for w in ["news", "latest", "recent", "today", "2024", "2025"]):
        from datetime import datetime
        year = datetime.now().year
        queries = [
            f"{topic} {year}",
            f"{topic} news {year}",
            f"latest {topic} updates {year}",
            f"{topic} breakthroughs {year}",
            f"{topic} site:techcrunch.com OR site:wired.com OR site:reuters.com",
        ]
    # AI / ML specific queries
    elif any(w in obj_lower for w in ["ai", "artificial intelligence", "machine learning", "llm"]):
        queries = [
            f"{topic} 2025",
            f"artificial intelligence breakthroughs 2025",
            f"AI machine learning news site:techcrunch.com OR site:wired.com",
            f"latest AI models tools 2025",
            f"AI research papers announcements 2025",
        ]
    elif "best" not in obj_lower and "top" not in obj_lower:
        queries.append(f"best {topic}")
        queries.append(f"{topic} reviews comparison")

    # Always deduplicate
    seen_q: list[str] = []
    for q in queries:
        if q and q not in seen_q:
            seen_q.append(q)
    return seen_q[:5]


def plan_deterministic(objective: str, max_pages: int) -> ResearchPlan:
    """Rule-based planner used when the LLM is unavailable."""
    log.info("Using deterministic planner for objective: %s", objective[:60])

    # Extract a short topic (first meaningful noun phrase)
    topic_match = re.search(
        r"(?:find|search|get|look for|compare|research|list|show)\s+(?:me\s+)?(.+?)(?:\s+and|\s+in|\s+under|\s+with|$)",
        objective,
        re.IGNORECASE,
    )
    topic = topic_match.group(1).strip() if topic_match else objective[:60].strip()

    constraints: list[str] = []
    constraints.extend(_extract_price_constraint(objective))
    constraints.extend(_extract_location(objective))

    required_fields = _infer_fields(objective)
    source_categories = _infer_source_categories(objective)
    search_queries = _generate_search_queries(objective, topic)

    extraction_schema = {field: f"Extract the {field} of the item" for field in required_fields}

    stopping_criteria = [
        f"All {len(required_fields)} required fields covered for at least 5 items",
        f"Crawl budget of {max_pages} pages reached",
        "Significant diminishing returns: 3 consecutive pages with relevance < 0.2",
    ]

    return ResearchPlan(
        topic=topic,
        constraints=constraints,
        required_fields=required_fields,
        search_queries=search_queries,
        source_categories=source_categories,
        ranking_priorities=["relevance", "completeness", "source_reliability"],
        max_pages=max_pages,
        stopping_criteria=stopping_criteria,
        extraction_schema=extraction_schema,
    )


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

async def create_research_plan(objective: str, max_pages: int) -> ResearchPlan:
    """
    Create a research plan using LLM if available, deterministic fallback otherwise.
    Always returns a valid ResearchPlan.
    """
    plan = await plan_with_llm(objective, max_pages)
    if plan:
        log.info("Research plan created via LLM for: %s", objective[:50])
        return plan

    log.info("LLM unavailable – using deterministic planner")
    return plan_deterministic(objective, max_pages)
