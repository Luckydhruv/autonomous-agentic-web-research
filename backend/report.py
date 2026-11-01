"""BART summaries and concise, source-linked report sections."""
from __future__ import annotations

import asyncio
import logging
import re
from functools import lru_cache
from pathlib import Path

from models import CrawlState, RankedResult, ResearchReport, ReportChunk, settings

log = logging.getLogger(__name__)


def _model_path() -> str:
    if settings.bart_model != "auto":
        return settings.bart_model
    root = Path(__file__).resolve().parent.parent
    if all((root / name).exists() for name in ("config.json", "model.safetensors", "tokenizer.json")):
        return str(root)
    return "facebook/bart-large-cnn"


@lru_cache(maxsize=1)
def _model():
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    path = _model_path()
    try:
        tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(path, local_files_only=True)
    except OSError:
        tokenizer = AutoTokenizer.from_pretrained(path)
        model = AutoModelForSeq2SeqLM.from_pretrained(path)
    model.eval()
    return tokenizer, model


def _extractive(text: str, max_words: int = 140) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(s.strip() for s in sentences if len(s.split()) > 5)[:max_words * 7]


def _summarize_sync(text: str) -> str:
    import torch
    tokenizer, model = _model()
    size = min(max(settings.bart_input_tokens, 128), 900)
    token_ids = tokenizer.encode(text, add_special_tokens=False)
    chunks = [token_ids[i:i + size] for i in range(0, len(token_ids), size)]
    summaries = []
    for ids in chunks:
        part = tokenizer.decode(ids, skip_special_tokens=True)
        inputs = tokenizer(part, return_tensors="pt", truncation=True, max_length=size)
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=settings.bart_summary_tokens, num_beams=4)
        summaries.append(tokenizer.decode(out[0], skip_special_tokens=True).strip())
    return "\n".join(s for s in summaries if s)


async def summarize(text: str) -> str:
    if not text.strip():
        return ""
    if not settings.bart_enabled or len(text.split()) < 40:
        return _extractive(text)
    try:
        return await asyncio.to_thread(_summarize_sync, text)
    except Exception as exc:
        log.warning("BART unavailable; using source extract: %s", exc)
        return _extractive(text)


async def generate_report(state: CrawlState, ranked: list[RankedResult]) -> ResearchReport:
    by_url = {record.url: record for record in state.records}
    chunks: list[ReportChunk] = []
    findings: list[str] = []

    for number, result in enumerate(ranked[:5], 1):
        record = by_url.get(result.url)
        if not record:
            continue
        evidence = record.relevant_text or record.main_text
        summary = " ".join((await summarize(evidence[:24000])).split()[:125])
        if not summary:
            summary = _extractive(evidence, 125)
        facts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", evidence)
                 if re.search(r"\d", s) and 8 < len(s.split()) <= 25]
        body = summary
        if facts:
            # Keep a short verbatim fact for precise figures without copying a page.
            body += "\n\nSource detail: “" + facts[0] + "”"
        chunks.append(ReportChunk(
            title=f"Source {number}: {record.title}", text=body,
            source_url=record.url,
        ))
        for sentence in re.split(r"(?<=[.!?])\s+", evidence):
            if re.search(r"\d|\b(?:found|reported|states|according|because|increased|decreased|offers|placed)\b", sentence, re.I):
                sentence = sentence.strip()
                if len(sentence) > 35 and sentence not in findings:
                    findings.append(sentence[:400])
                if len(findings) >= 8:
                    break

    if not chunks:
        chunks.append(ReportChunk(
            title="Retrieval status",
            text=(f"No crawlable source text was returned for: {state.objective}. "
                  "This run has no verified findings; retry when the local crawler can reach the search and source pages."),
        ))
    source_summaries = [chunk.text.split("\n\nSource detail:", 1)[0] for chunk in chunks if chunk.source_url]
    executive = " ".join(source_summaries[:2]) or chunks[0].text
    executive = " ".join(executive.split()[:130])
    if len(chunks) > 1:
        chunks.append(ReportChunk(
            title="Cross-source reading",
            text=(f"The report compares {len(source_summaries)} retrieved source pages. "
                  "Figures from different years, cohorts, or definitions are kept separate. "
                  "Use the source links to check each statement in its original context."),
        ))
    if state.missing_fields:
        chunks.append(ReportChunk(
            title="Gaps and limits",
            text=("Requested details not confirmed in the extracted text: "
                  + ", ".join(state.missing_fields) + ". "
                  + (state.stopping_reason or "The crawl budget or accessible source text may be incomplete.")),
        ))
    narrative = "\n\n".join(f"{c.title}\n{c.text}" for c in chunks)
    sources = list(dict.fromkeys(c.source_url for c in chunks if c.source_url))
    return ResearchReport(
        task_id=state.task_id,
        objective=state.objective,
        executive_summary=executive,
        report_narrative=narrative,
        report_chunks=chunks,
        top_source_title=ranked[0].title if ranked else "",
        top_source_url=ranked[0].url if ranked else "",
        relevant_source_text=by_url[ranked[0].url].relevant_text[:8000] if ranked and ranked[0].url in by_url else "",
        bart_summary=source_summaries[0] if source_summaries else "",
        methodology=(f"Crawl4AI fetched search pages and up to {state.plan.max_pages} source pages; "
                     "source text was ranked by topic match and summarized in token-safe BART chunks."),
        ranked_results=ranked,
        key_findings=findings[:8] or ["No factual finding could be verified from source text."],
        limitations=([state.stopping_reason] if state.stopping_reason else []) +
                    ([f"Unresolved details: {', '.join(state.missing_fields)}"] if state.missing_fields else []),
        sources=sources,
        crawl_stats={"pages_crawled": state.pages_crawled, "records_collected": len(state.records),
                     "results_ranked": len(ranked), "failed_urls": len(state.failed_urls)},
        confidence_score=round(sum(r.final_score for r in ranked[:5]) / max(len(ranked[:5]), 1), 2),
    )
