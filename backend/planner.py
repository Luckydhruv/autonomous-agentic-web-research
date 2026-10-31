"""
planner.py – Convert a natural-language research objective into a
validated ResearchPlan with high-precision search queries and extraction schema.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from config import settings
from llm_client import generate_json
from schemas import ResearchPlan

log = logging.getLogger(__name__)

MIN_PAGES = 10


# ──────────────────────────────────────────────
# Deterministic fallback planner
# ──────────────────────────────────────────────

def _extract_price_constraint(text: str) -> list[str]:
    constraints = []
    # Match under 1lakh, under 1 lakh, under 100000, under 50k, etc.
    patterns = [
        r"(?:under|below|less than|up to|within|max(?:imum)?)\s*[\$₹€£]?\s*[\d,]+(?:\s*(?:lakhs?|cr|k|million))?",
        r"(?:under|below|less than|within)\s*(?:1\s*lakh|100000|₹1,00,000|50000|80000)",
    ]
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            val = m.group(0).strip()
            if val not in constraints:
                constraints.append(val)
    return constraints


def _extract_location(text: str) -> list[str]:
    location_keywords = [
        r"\bin\s+(india|usa|uk|us|canada|australia|germany|france|japan)\b",
    ]
    for pattern in location_keywords:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return [m.group(0).strip()]
    return []


def _infer_fields(objective: str) -> list[str]:
    obj_lower = objective.lower()
    if any(kw in obj_lower for kw in ["laptop", "computer", "pc", "notebook"]):
        return ["name", "price", "cpu", "gpu", "ram", "storage", "display", "suitability"]
    if any(kw in obj_lower for kw in ["phone", "smartphone", "mobile"]):
        return ["name", "price", "processor", "camera", "battery", "display"]
    if any(kw in obj_lower for kw in ["placement", "salary", "package", "job", "career"]):
        return ["highest_package", "average_package", "placement_rate", "top_recruiters", "year"]
    if any(kw in obj_lower for kw in ["hotel", "resort", "accommodation"]):
        return ["name", "price_per_night", "location", "rating", "amenities"]
    if any(kw in obj_lower for kw in ["news", "latest", "breakthrough", "update", "ai"]):
        return ["headline", "summary", "key_developments", "date", "source"]

    return ["title", "summary", "key_stats", "date", "source"]


def _clean_topic_core(objective: str) -> str:
    """Extract the clean subject from an objective, stripping command verbs and fluff."""
    text = objective.strip()
    # Strip leading command phrasing
    text = re.sub(
        r"^(?:please\s+)?(?:find|search(?:\s+for)?|get|look\s+for|give\s+me|show\s+me|tell\s+me\s+about|list|compare)\s+(?:me\s+)?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^(?:the\s+)?(?:top\s+\d+|best|latest)\s+", "", text, flags=re.IGNORECASE)
    # Strip trailing compare/recommend phrases
    text = re.split(r"[,;]\s*(?:compare|recommend|and recommend|suggest|review|list)", text, flags=re.IGNORECASE)[0]
    text = re.sub(r"\s+and\s+(?:compare|recommend|suggest).*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _generate_search_queries(objective: str, topic: str) -> list[str]:
    """Generate 5-7 distinct, high-precision search queries."""
    obj_lower = objective.lower()
    queries = []

    # 1. Topic: Laptops / Tech Products
    if "laptop" in obj_lower:
        price_tag = "under 1 lakh" if any(w in obj_lower for w in ["1lakh", "1 lakh", "100000", "1,00,000"]) else ""
        role_tag = "software engineer coding programming" if any(w in obj_lower for w in ["software", "developer", "engineer", "coding", "programming"]) else ""

        if price_tag and role_tag:
            queries.append(f"best laptops {price_tag} for software engineers coding India")
            queries.append(f"top laptops {price_tag} for programming specs comparison")
            queries.append(f"best developer laptops {price_tag} India review")
            queries.append(f"MacBook Air vs Lenovo vs ASUS laptops {price_tag} for coding")
            queries.append(f"top 10 laptops {price_tag} in India specs")
        elif price_tag:
            queries.append(f"best laptops {price_tag} in India specs review")
            queries.append(f"top 10 laptops {price_tag} comparison")
            queries.append(f"best performance laptops {price_tag}")
        else:
            queries.append(f"best laptops for software engineers 2026")
            queries.append(f"top programming laptops comparison review")

    # 2. Topic: Placements / Education
    elif any(w in obj_lower for w in ["placement", "package", "salary", "recruit"]):
        queries.append(f"{topic} highest average package")
        queries.append(f"{topic} official report")
        queries.append(f"{topic} top companies recruiters")
        queries.append(f"{topic} branch wise placement")

    # 3. Topic: AI / News / General
    elif any(w in obj_lower for w in ["news", "latest", "breakthrough", "update", "ai"]):
        queries.append(f"{topic} 2026")
        queries.append(f"{topic} latest updates breakthroughs")
        queries.append(f"{topic} overview report")
        queries.append(f"recent developments in {topic}")

    # 4. General fallback
    else:
        queries.append(f"best {topic} comparison review")
        queries.append(f"top {topic} guide overview")
        queries.append(f"{topic} specifications details")

    # Deduplicate queries
    seen = set()
    unique = []
    for q in queries:
        clean_q = q.strip()
        if clean_q and clean_q.lower() not in seen:
            seen.add(clean_q.lower())
            unique.append(clean_q)

    return unique[:6]


def plan_deterministic(objective: str, max_pages: int) -> ResearchPlan:
    """Rule-based planner producing focused queries and schema."""
    log.info("Generating deterministic research plan for: %s", objective[:60])

    core = _clean_topic_core(objective)
    # Ensure readable topic
    topic = core if len(core.split()) <= 8 else " ".join(core.split()[:8])
    if not topic:
        topic = objective[:50].strip()

    constraints = _extract_price_constraint(objective)
    constraints.extend(_extract_location(objective))

    required_fields = _infer_fields(objective)
    search_queries = _generate_search_queries(objective, topic)

    extraction_schema = {field: f"Extract {field}" for field in required_fields}

    stopping_criteria = [
        f"At least {max(max_pages, MIN_PAGES)} pages evaluated",
        "Key comparative fields identified",
    ]

    return ResearchPlan(
        topic=topic,
        constraints=constraints,
        required_fields=required_fields,
        search_queries=search_queries,
        source_categories=["tech reviews", "benchmarks", "official specifications", "buying guides"],
        ranking_priorities=["relevance", "completeness", "specifications_accuracy"],
        max_pages=max(max_pages, MIN_PAGES),
        stopping_criteria=stopping_criteria,
        extraction_schema=extraction_schema,
    )


async def create_research_plan(objective: str, max_pages: int) -> ResearchPlan:
    """Create a research plan. Uses deterministic planner for instant response and 0 token waste."""
    return plan_deterministic(objective, max_pages)
