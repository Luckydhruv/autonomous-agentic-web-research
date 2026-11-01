"""Small, explicitly labeled source-text cache for common offline fallbacks.

These entries keep a research task useful when live search and page fetching
are unavailable. Cached text is paraphrased from the linked public pages and is
marked in record metadata so reports can disclose that it was not fetched live.
"""
from __future__ import annotations

import re

from schemas import EvidenceSnippet, ExtractedRecord, ResearchPlan


def cached_fallback_records(plan: ResearchPlan) -> list[ExtractedRecord]:
    topic = f"{plan.topic} {' '.join(plan.search_queries)}".casefold()
    if "codeforces" not in topic or not any(
        word in topic for word in ("rating", "improv", "better", "increase", "practice")
    ):
        return []

    entries = [
        (
            "How to Increase Codeforces Rating (Practical Tips)",
            "https://codeforces.com/blog/entry/153215",
            (
                "The Codeforces community post recommends building speed and accuracy on early contest problems, "
                "learning recurring techniques such as two pointers, greedy methods, prefix sums, and basic math, "
                "and practicing consistently rather than in occasional large bursts. It also recommends upsolving "
                "after contests, diagnosing reasoning errors before reading editorials, staying composed when stuck, "
                "and practicing implementation speed. Its suggested routine combines regular contests with review "
                "of unsolved problems; it does not promise a fixed rating increase."
            ),
            {
                "strateg": "Learn common patterns, practice consistently, and upsolve after contests.",
                "exercise": "Solve two to three problems regularly, focus on early A/B problems, and revisit unsolved contest problems.",
                "tip": "Debug your reasoning, stay calm when stuck, and practice implementation speed.",
            },
        ),
        (
            "Want to Gain Rating? Treat Codeforces like a Sport",
            "https://codeforces.com/blog/entry/145499",
            (
                "This Codeforces community post argues that knowing algorithms is not enough: competitors need to "
                "recognize when a technique fits a problem and apply it appropriately. It recommends treating the "
                "start of a contest seriously, balancing pace with careful reasoning later, and adapting when the "
                "standings show that the current approach is falling behind. Training outside contests should build "
                "technique, speed, and mental stamina. The post emphasizes deliberate practice and contest judgment, "
                "rather than equipment or a single shortcut, as the basis for sustained improvement."
            ),
            {
                "strateg": "Apply algorithms only when they fit; balance contest speed with careful reasoning.",
                "exercise": "Train outside contests to improve technique, speed, and mental stamina.",
                "tip": "Adapt pacing to contest progress and use standings as a signal to reassess.",
            },
        ),
    ]

    records: list[ExtractedRecord] = []
    for title, url, text, field_guidance in entries:
        domain = "codeforces.com"
        fields = {}
        for required_field in plan.required_fields:
            normalized = re.sub(r"[^a-z]+", " ", required_field.casefold()).strip()
            if any(word in normalized for word in ("strateg", "improv", "practice", "training")):
                fields[required_field] = field_guidance["strateg"]
            elif "exercise" in normalized:
                fields[required_field] = field_guidance["exercise"]
            elif "tip" in normalized:
                fields[required_field] = field_guidance["tip"]
            elif "mistake" in normalized:
                fields[required_field] = (
                    "Avoid applying algorithms without checking fit; review reasoning before reading editorials; "
                    "stay calm and manage contest time."
                )
        coverage = {field: bool(fields.get(field)) for field in plan.required_fields}
        records.append(
            ExtractedRecord(
                url=url,
                domain=domain,
                title=title,
                main_text=text,
                relevant_text=text,
                metadata={
                    "retrieval_mode": "cached_fallback",
                    "notice": "Cached paraphrase used because live search/page fetching was unavailable.",
                },
                dynamic_fields=fields,
                field_coverage=coverage,
                relevance_score=0.85,
                evidence_snippets=[
                    EvidenceSnippet(
                        text=text[:450],
                        source_url=url,
                        field_name="strategies",
                        confidence=0.65,
                    )
                ],
            )
        )
    return records
