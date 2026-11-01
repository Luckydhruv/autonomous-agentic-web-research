"""Content-focused text cleanup and topic-relevant passage selection."""
from __future__ import annotations

import re

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "best", "by", "compare",
    "do", "find", "for", "from", "get", "give", "in", "latest", "me",
    "of", "on", "or", "please", "recent", "research", "search", "show",
    "some", "the", "this", "today", "updates", "with", "about", "news",
}
_BOILERPLATE = re.compile(
    r"\b(search|subscribe|newsletter|sign\s*up|log\s*in|sign\s*in|"
    r"cookie preferences|privacy policy|terms of service|all rights reserved|"
    r"your browser does not support|skip to content|read more|share this|"
    r"follow us|related articles|advertisement|stay ahead of the curve|"
    r"curated articles and updates|curated daily by|"
    r"the tips tools and templates|"
    r"keeping up is a full-time job|makes it manageable)\b",
    re.IGNORECASE,
)
_ALIASES = {
    "ai": ("ai", "artificial intelligence", "machine learning", "generative ai", "llm", "gpt", "openai", "gemini", "claude"),
    "laptop": ("laptop", "notebook", "chromebook"),
    "phone": ("phone", "smartphone", "mobile"),
}
_NON_ARTICLE_URL = re.compile(
    r"/(?:workshops?|training|corporate-training|courses?|quiz|refund-policy|"
    r"privacy-policy|terms(?:-of-service)?|contact|about|pricing|events?)(?:/|\?|$)",
    re.IGNORECASE,
)
_NON_ARTICLE_TITLE = re.compile(
    r"\b(?:workshops?|courses?|training|corporate training|refund policy|privacy policy|"
    r"terms of service|daily quiz|live events?)\b",
    re.IGNORECASE,
)
_PLACEMENT_TOPIC = re.compile(
    r"\bplacements?\b",
    re.IGNORECASE,
)
_PLACEMENT_METRIC = re.compile(
    r"\b(highest|average|avg\.?|median|lowest|package|ctc|salary|salaries|"
    r"placement rate|placed|recruited|recruiters?|companies visited|offers?|students placed)\b",
    re.IGNORECASE,
)
_PROMOTIONAL_COPY = re.compile(
    r"\b(leading|renowned|robust track record|excellent placement outcomes|"
    r"compelling opportunities|industry connect|top placements|specialized curriculum|"
    r"offers? compelling|one of india.s leading)\b",
    re.IGNORECASE,
)
_PLACEMENT_VALUE = re.compile(
    r"(?:₹|\bINR\b|\bRs\.?\s*)?\s*\d[\d,.]*\s*"
    r"(?:LPA\b|Lakhs?\b|Crores?\b|Cr\.?\b|%|percent\b|students?\b|"
    r"offers?\b|companies\b|recruiters?\b)",
    re.IGNORECASE,
)
_PLACEMENT_LABELED_VALUE = re.compile(
    r"\b(?:highest|average|avg\.?|median|lowest)\s+(?:package|ctc|salary)\s*"
    r"(?:[:=\-]|\b(?:is|of|at|was|stood\s+at)\b)\s*"
    r"(?:₹|\bINR\b|\bRs\.?\s*)?\s*\d[\d,.]*"
    r"|\b(?:placement\s+rate|students?\s+placed|offers?|recruiters?|companies)\s*"
    r"(?:[:=\-]|\b(?:is|are|of|at|was|were)\b)\s*\d[\d,.]*",
    re.IGNORECASE,
)
_PLACEMENT_UNIT_HINT = re.compile(r"\b(?:LPA|Lakhs?|Crores?|Cr\.?)\b|%", re.I)
_PLACEMENT_COUNT_LABEL = re.compile(
    r"\b(?:number|no\.?|count|total)\s+of\s+(?:students?|offers?|recruiters?|companies)\b|"
    r"\b(?:placement\s+rate|students?\s+placed|offers?|recruiters?|companies\s+visited)\s*[:=]",
    re.IGNORECASE,
)


def is_non_article_page(url: str, title: str = "") -> bool:
    """Identify navigation, promotion, policy, and event pages for article research."""
    from urllib.parse import urlparse

    if urlparse(url).path in ("", "/"):
        return True
    return bool(_NON_ARTICLE_URL.search(url) or _NON_ARTICLE_TITLE.search(title))


def clean_content_lines(
    text: str, min_length: int = 25, allow_numeric: bool = False,
) -> list[str]:
    """Drop repeated UI copy, tiny labels, and empty fragments from page text."""
    lines: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"\n+", text):
        line = re.sub(r"\s+", " ", raw).strip(" \t|•·")
        key = line.casefold()
        if len(line) < min_length or key in seen or _BOILERPLATE.search(line):
            continue
        if not allow_numeric and re.fullmatch(r"[\W\d_]+", line):
            continue
        seen.add(key)
        lines.append(line)
    return lines


def _topic_terms(topic: str) -> list[str]:
    return list(dict.fromkeys(
        token for token in re.findall(r"[a-z0-9]+", topic.casefold())
        if len(token) > 1 and token not in _STOP_WORDS
    ))


def extract_relevant_text(
    text: str, topic: str, title: str = "", max_chars: int = 3500,
) -> str:
    """Select useful passages from a page using the research topic, not page chrome."""
    placement_topic = bool(_PLACEMENT_TOPIC.search(topic))
    lines = clean_content_lines(
        text,
        min_length=1 if placement_topic else 25,
        allow_numeric=placement_topic,
    )
    if not lines:
        return ""

    # Placement-statistics searches need figures, not generic institute marketing.
    # Keep a metric label together with an adjacent value line when the page splits
    # a table row across separate text lines.
    if placement_topic:
        metric_indices: set[int] = set()
        for index, line in enumerate(lines):
            if not _PLACEMENT_METRIC.search(line) or _PROMOTIONAL_COPY.search(line):
                continue
            has_metric_value = bool(
                _PLACEMENT_VALUE.search(line) or _PLACEMENT_LABELED_VALUE.search(line)
            )
            nearby_value = any(
                _PLACEMENT_VALUE.search(lines[neighbor])
                or (
                    (_PLACEMENT_UNIT_HINT.search(line) or _PLACEMENT_COUNT_LABEL.search(line))
                    and re.search(r"(?<!\w)\d[\d,.]*(?!\w)", lines[neighbor])
                )
                for neighbor in (index - 1, index + 1)
                if 0 <= neighbor < len(lines)
            )
            if has_metric_value or nearby_value:
                metric_indices.add(index)
                for neighbor in (index - 1, index + 1):
                    if not 0 <= neighbor < len(lines):
                        continue
                    explicit_value = bool(_PLACEMENT_VALUE.search(lines[neighbor]))
                    table_value = bool(
                        (_PLACEMENT_UNIT_HINT.search(line) or _PLACEMENT_COUNT_LABEL.search(line))
                        and re.fullmatch(r"\s*\d[\d,.]*\s*", lines[neighbor])
                    )
                    if explicit_value or table_value:
                        metric_indices.add(neighbor)
        if metric_indices:
            return "\n".join(lines[index] for index in sorted(metric_indices))[:max_chars].strip()
        # If no figures are present, avoid feeding promotional copy to BART.
        return ""

    terms = _topic_terms(topic)
    if not terms:
        selected = lines[:]
    else:
        aliases: dict[str, tuple[str, ...]] = {}
        for term in terms:
            aliases[term] = _ALIASES.get(term, (term,))

        def score(line: str) -> float:
            haystack = line.casefold()
            hits = 0
            for options in aliases.values():
                if any(re.search(rf"(?<!\w){re.escape(option)}(?!\w)", haystack) for option in options):
                    hits += 1
            # The title is relevant context, but the page body must also match.
            return hits / len(aliases)

        line_scores = [score(line) for line in lines]
        selected_indices: set[int] = {
            index for index, value in enumerate(line_scores) if value >= 0.5
        }
        # Keep a nearby explanatory sentence with each matching headline or paragraph.
        for index in list(selected_indices):
            if index + 1 < len(lines) and len(lines[index + 1]) >= 45:
                selected_indices.add(index + 1)
        selected = [lines[i] for i in sorted(selected_indices)]
        detailed = [line for line in selected if len(line.split()) >= 12]
        if len(detailed) >= 2:
            selected = detailed

        if not selected:
            # The record is still a useful fallback when a page uses different wording.
            selected = lines[:8]

    title = re.sub(r"\s+", " ", title).strip()
    # The title is metadata, not evidence; passing it into BART often causes the
    # generated summary to repeat a promotional page headline.
    result = "\n".join(line for line in selected if line.casefold() != title.casefold())
    return result[:max_chars].strip()


def has_useful_content(text: str) -> bool:
    """A page needs multiple substantive lines before it can be a fallback result."""
    lines = clean_content_lines(text)
    return len(lines) >= 2 and sum(len(line.split()) for line in lines) >= 35
