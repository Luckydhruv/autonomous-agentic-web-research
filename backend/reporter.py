"""
reporter.py – Generate a structured ResearchReport from crawl results.
LLM is used for the executive summary and key findings when available.
The fallback produces a template-based report from the data.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from bart_summarizer import summarize_page
from content_utils import clean_content_lines, extract_relevant_text
from ranking import rank_records
from schemas import (
    CrawlState, RankedResult, ResearchPlan, ResearchReport
)

log = logging.getLogger(__name__)


def _placement_objective(objective: str) -> bool:
    return bool(re.search(r"\bplacements?\b", objective, re.IGNORECASE))


def _evidence_sources(state: CrawlState, ranked: list[RankedResult]) -> list[dict[str, Any]]:
    """Pair ranked pages with topic-filtered body text, excluding title-only evidence."""
    is_placement = _placement_objective(state.objective)
    records_by_url = {record.url: record for record in state.records}
    sources: list[dict[str, Any]] = []
    for result in ranked[:10]:
        record = records_by_url.get(result.url)
        if not record or not record.main_text.strip():
            continue
        evidence = extract_relevant_text(record.main_text, state.objective, record.title)
        if not evidence:
            continue
        sources.append({
            "record": record,
            "result": result,
            "title": record.title or result.title,
            "url": record.url,
            "domain": record.domain or result.domain,
            "date": record.published_date,
            "text": evidence,
            "lines": clean_content_lines(
                evidence,
                min_length=1 if is_placement else 25,
                allow_numeric=is_placement,
            ),
            "summary": "",
        })
    return sources


def _report_narrative(
    objective: str,
    sources: list[dict[str, Any]],
    state: CrawlState,
    ranked: list[RankedResult],
) -> str:
    """Build a cited, evidence-first report without word-count padding."""
    is_placement = _placement_objective(objective)
    if not sources:
        if is_placement:
            return (
                f"Research question: {objective}. The crawl returned {len(ranked)} ranked result(s) "
                f"from {state.pages_crawled} page fetches, but the extracted article text did not "
                "contain a verifiable placement figure. In particular, this run could not confirm "
                "an average or median package, highest package, placement rate, number of students "
                "placed, or recruiter and offer totals. A page headline or general claim about a "
                "strong placement record is not a statistic, so none is presented as one here. "
                "The result is inconclusive for the requested statistics and needs sources that "
                "publish cohort-level placement data, such as an institute placement report or "
                "official annual report."
            )
        return (
            f"Research question: {objective}. The crawl fetched {state.pages_crawled} pages and "
            f"ranked {len(ranked)} results, but none yielded enough topic-matched body text to "
            "support a factual synthesis. The result is inconclusive; page titles alone are not "
            "treated as evidence."
        )

    parts = [f"Research question: {objective}."]
    if is_placement:
        parts.append(
            f"This review found {len(sources)} source page(s) with placement-related numeric evidence. "
            "The figures below are reported as each source states them; they should not be combined "
            "unless the graduating cohort, branch coverage, and definition of placement match."
        )
    else:
        parts.append(
            f"The synthesis uses {len(sources)} of the {len(ranked)} ranked pages that contained "
            "topic-matched body text. Each source is identified so its context can be checked."
        )

    for index, source in enumerate(sources[:5], start=1):
        summary = source.get("summary", "").strip()
        exact_text = " ".join(source["lines"])
        description = summary if summary else exact_text
        if is_placement and exact_text:
            # Preserve the source's exact metric wording alongside BART's prose.
            section = f"Source {index}, {source['title']} ({source['domain']}): {description}"
            if summary and exact_text.casefold() not in summary.casefold():
                section += f" Exact extracted figures: {exact_text}."
        else:
            section = f"Source {index}, {source['title']} ({source['domain']}): {description}"
        if source.get("date"):
            section += f" The page metadata gives its date as {source['date']}."
        section += f" Source URL: {source['url']}"
        parts.append(section)

    if is_placement:
        parts.append(
            "Interpretation: a highest-package figure describes the top offer, not typical pay. "
            "An average can be skewed by a small number of high offers, so a median, placement "
            "rate, number eligible, number placed, and branch-wise breakdown provide a stronger "
            "picture when the source publishes them. Package figures are also not take-home salary."
        )
    elif len(sources) > 1:
        parts.append(
            "Cross-source reading: treat repeated claims as stronger only when sources provide "
            "independent evidence and comparable dates or definitions. Where pages use different "
            "periods or measures, the report keeps their statements separate instead of averaging them."
        )

    missing = ", ".join(state.missing_fields)
    if missing:
        parts.append(f"Unresolved requested fields: {missing}.")
    parts.append(
        f"Scope and limitations: the crawl fetched {state.pages_crawled} pages and ranked "
        f"{len(ranked)} results under a finite crawl budget. These counts describe this run, "
        "not the whole web. Confirm important figures in the original source, especially where "
        "a page is a third-party summary rather than an institute publication."
    )
    return "\n\n".join(parts)


def _summary_findings(summary: str) -> list[str]:
    """Turn the BART summary into short, source-grounded finding bullets."""
    sentences = re.split(r"(?<=[.!?])\s+", summary.strip())
    return [sentence.strip() for sentence in sentences if len(sentence.strip()) > 25][:5]


# ──────────────────────────────────────────────
# Deterministic fallback
# ──────────────────────────────────────────────

def _fallback_summary(
    objective: str, ranked: list[RankedResult], state: CrawlState
) -> str:
    count = len(ranked)
    domains = list({r.domain for r in ranked[:5]})
    missing_note = (
        f" Some requested information was not found: {', '.join(state.missing_fields)}."
        if state.missing_fields else ""
    )
    return (
        f"Research completed for '{objective}'. "
        f"Crawled {state.pages_crawled} pages and collected {count} relevant results "
        f"from {len(domains)} unique sources including {', '.join(domains[:3])}."
        f"{missing_note} "
        f"Results are ranked by relevance, source quality, freshness, and completeness."
    )


def _fallback_findings(ranked: list[RankedResult], plan: ResearchPlan) -> list[str]:
    findings = []
    if ranked:
        top = ranked[0]
        findings.append(f"Top result: '{top.title}' from {top.domain} (score: {top.final_score:.2f})")
        # Field-based findings
        all_fields: dict[str, list[str]] = {}
        for r in ranked[:10]:
            dynamic_fields = r.dynamic_fields if isinstance(r.dynamic_fields, dict) else {}
            for f, v in dynamic_fields.items():
                all_fields.setdefault(f, []).append(str(v))
        for field, values in list(all_fields.items())[:4]:
            unique = list(dict.fromkeys(values))[:3]
            findings.append(f"'{field}' values found: {', '.join(unique)}")
    return findings


# ──────────────────────────────────────────────
# Comparison table builder
# ──────────────────────────────────────────────

def _build_comparison_table(
    ranked: list[RankedResult], plan: ResearchPlan
) -> list[dict[str, Any]]:
    """Build a flat list of dicts for a tabular comparison."""
    rows = []
    for r in ranked[:15]:
        row: dict[str, Any] = {
            "rank": r.rank,
            "title": r.title[:80],
            "domain": r.domain,
            "score": r.final_score,
            "url": r.url,
        }
        # Add dynamic fields
        for field in plan.required_fields:
            if field in row:
                continue
            dynamic_fields = r.dynamic_fields if isinstance(r.dynamic_fields, dict) else {}
            row[field] = dynamic_fields.get(field, "—")
        rows.append(row)
    return rows


# ──────────────────────────────────────────────
# Main public function
# ──────────────────────────────────────────────

async def generate_report(
    state: CrawlState,
    ranked: list[RankedResult],
) -> ResearchReport:
    plan = state.plan
    objective = state.objective

    # Build evidence from several top-ranked pages; titles and page descriptions
    # alone are not sufficient evidence for a factual report.
    evidence_sources = _evidence_sources(state, ranked)
    for source in evidence_sources[:5]:
        source["summary"] = await summarize_page(source["text"])

    top_result = ranked[0] if ranked else None
    lead_source = evidence_sources[0] if evidence_sources else None
    top_record = lead_source["record"] if lead_source else next(
        (record for record in state.records if top_result and record.url == top_result.url), None
    )
    source_title = lead_source["title"] if lead_source else (top_result.title if top_result else "")
    source_url = lead_source["url"] if lead_source else (top_result.url if top_result else "")
    relevant_text = lead_source["text"] if lead_source else ""
    bart_summary = lead_source["summary"] if lead_source else ""
    if top_record:
        top_record.relevant_text = relevant_text
        top_record.page_summary = bart_summary

    is_placement = _placement_objective(objective)
    if is_placement and not evidence_sources:
        summary = (
            "The retrieved pages did not provide verifiable placement statistics. "
            "No average or median package, highest package, placement rate, placed-student "
            "count, or recruiter total could be confirmed from the extracted source text."
        )
        findings = ["No verifiable placement figures were present in the retrieved source text."]
    else:
        summary = bart_summary or (
            " ".join(relevant_text.split()[:90])
            if relevant_text else _fallback_summary(objective, ranked, state)
        )
        findings = []
        for index, source in enumerate(evidence_sources[:5], start=1):
            for line in source["lines"]:
                finding = f"{line} (Source {index}: {source['domain']})"
                if finding not in findings:
                    findings.append(finding)
                if len(findings) >= 6:
                    break
            if len(findings) >= 6:
                break
        if not findings:
            findings = _summary_findings(bart_summary) or _fallback_findings(ranked, plan)

    narrative = _report_narrative(objective, evidence_sources, state, ranked)

    # Comparison table
    comparison = _build_comparison_table(ranked, plan)

    # Limitations
    limitations: list[str] = []
    if is_placement and not evidence_sources:
        limitations.append(
            "No numeric placement data was extracted; the report does not turn promotional placement claims into statistics."
        )
    if state.missing_fields:
        limitations.append(f"Information for these fields was not found: {', '.join(state.missing_fields)}")
    if any(
        isinstance(record.metadata, dict)
        and record.metadata.get("retrieval_mode") == "cached_fallback"
        for record in state.records
    ):
        limitations.append(
            "Live search or page fetching was unavailable; cached paraphrases of the linked Codeforces posts were used and should be checked against the original pages."
        )
    if any(
        isinstance(record.metadata, dict)
        and record.metadata.get("retrieval_mode") == "search_preview"
        for record in state.records
    ):
        limitations.append(
            "Some results use search-result preview text because the linked article could not be fetched; open the source link for the full article."
        )
    if state.stopping_reason:
        limitations.append(f"Research stopped because: {state.stopping_reason}")
    if state.pages_crawled >= plan.max_pages:
        limitations.append("Crawl budget was reached; more sources may exist")
    if not any(
        isinstance(record.metadata, dict)
        and record.metadata.get("retrieval_mode") in {"cached_fallback", "search_preview"}
        for record in state.records
    ):
        limitations.append("Results reflect information publicly available at crawl time; details may have changed")

    # Sources
    sources = list(dict.fromkeys(r.url for r in ranked[:20]))

    # Crawl stats
    crawl_stats = {
        "pages_crawled": state.pages_crawled,
        "records_collected": len(state.records),
        "results_ranked": len(ranked),
        "stopping_reason": state.stopping_reason or "Research completed",
        "failed_urls": len(state.failed_urls),
        "missing_fields": state.missing_fields,
    }

    # Confidence: based on coverage and source count
    coverage_avg = (
        sum(r.final_score for r in ranked) / len(ranked) if ranked else 0.0
    )
    confidence = round(min(coverage_avg * 1.2, 1.0), 3)

    # Determine whether LLM was used (at least one dynamic field was extracted via LLM)
    records_by_url = {record.url: record for record in state.records}
    cached_used = any(
        isinstance(record.metadata, dict)
        and record.metadata.get("retrieval_mode") == "cached_fallback"
        for record in state.records
    )
    preview_used = any(
        isinstance(record.metadata, dict)
        and record.metadata.get("retrieval_mode") == "search_preview"
        for record in state.records
    )
    llm_used = any(
        r.dynamic_fields
        and not (
            isinstance(records_by_url.get(r.url).metadata, dict)
            and records_by_url[r.url].metadata.get("retrieval_mode") in {"cached_fallback", "search_preview"}
        )
        for r in ranked if r.url in records_by_url
    )
    field_method = (
        "LLM-assisted" if llm_used else
        "cached source annotations" if cached_used else
        "search-preview text" if preview_used else
        "pattern-based"
    )

    methodology = (
        f"1. Parsed research objective into a structured plan with {len(plan.required_fields)} fields. "
        f"2. Generated {len(plan.search_queries)} search queries. "
        f"3. Adaptively crawled up to {plan.max_pages} pages, prioritising by relevance and coverage. "
        f"4. Extracted topic-matched passages and used local BART on up to five sources with usable text; structured fields used {field_method}. "
        f"{'A labeled cached passage was used where live page fetching failed. ' if cached_used else ''}"
        f"{'Search-result previews were used for sources that could not be fetched. ' if preview_used else ''}"
        f"5. Ranked {len(ranked)} results using weighted scoring (relevance, reliability, freshness, completeness)."
    )

    return ResearchReport(
        task_id=state.task_id,
        objective=objective,
        executive_summary=summary,
        report_narrative=narrative,
        top_source_title=source_title,
        top_source_url=source_url,
        relevant_source_text=relevant_text,
        bart_summary=bart_summary,
        methodology=methodology,
        ranked_results=ranked,
        comparison_table=comparison,
        key_findings=findings,
        limitations=limitations,
        conflicting_information=state.conflicting_fields,
        sources=sources,
        crawl_stats=crawl_stats,
        generated_at=datetime.utcnow(),
        confidence_score=confidence,
    )
