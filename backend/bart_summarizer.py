"""Token-aware, local BART page summarization with a no-token fallback."""
from __future__ import annotations

import asyncio
import logging
import re
from functools import lru_cache

from config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_model():
    # Lazy loading keeps startup lightweight; Hugging Face caches weights locally.
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    try:
        # Prefer the persistent cache so normal summarization never needs a token or
        # a network request after the first model download.
        tokenizer = AutoTokenizer.from_pretrained(
            settings.bart_model, local_files_only=True
        )
        model = AutoModelForSeq2SeqLM.from_pretrained(
            settings.bart_model, local_files_only=True
        )
    except OSError:
        # First use: fetch and cache the model and tokenizer from Hugging Face.
        tokenizer = AutoTokenizer.from_pretrained(settings.bart_model)
        model = AutoModelForSeq2SeqLM.from_pretrained(settings.bart_model)
    model.eval()
    return tokenizer, model


def _fallback_summary(text: str, max_chars: int = 1800) -> str:
    """Return a compact sentence-based summary if model weights are unavailable."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    selected = [s.strip() for s in sentences if len(s.strip()) > 35][:8]
    return " ".join(selected)[:max_chars]


def _summarize_sync(text: str) -> str:
    import torch

    tokenizer, model = _load_model()
    ids = tokenizer.encode(text, add_special_tokens=False)
    chunk_size = max(128, min(settings.bart_input_tokens, 1000))
    chunks = [ids[i:i + chunk_size] for i in range(0, len(ids), chunk_size)]
    summaries: list[str] = []
    for chunk_ids in chunks:
        chunk = tokenizer.decode(chunk_ids, skip_special_tokens=True)
        if not chunk.strip():
            continue
        inputs = tokenizer(
            chunk,
            return_tensors="pt",
            truncation=True,
            max_length=chunk_size,
        )
        with torch.inference_mode():
            summary_ids = model.generate(
                **inputs,
                max_new_tokens=settings.bart_summary_tokens,
                min_length=min(24, max(12, settings.bart_summary_tokens // 4)),
                num_beams=4,
                do_sample=False,
                early_stopping=True,
            )
        summary = tokenizer.decode(summary_ids[0], skip_special_tokens=True).strip()
        if summary:
            summaries.append(summary)

    combined = " ".join(summaries)
    if len(summaries) > 1 and len(tokenizer.encode(combined)) > chunk_size:
        # One consolidation pass keeps stored summaries compact on long pages.
        inputs = tokenizer(
            combined, return_tensors="pt", truncation=True, max_length=chunk_size
        )
        with torch.inference_mode():
            summary_ids = model.generate(
                **inputs,
                max_new_tokens=220,
                min_length=80,
                num_beams=4,
                do_sample=False,
                early_stopping=True,
            )
        combined = tokenizer.decode(summary_ids[0], skip_special_tokens=True).strip()
    return combined[:5000]


async def summarize_page(text: str) -> str:
    """Summarize extracted page text without blocking the async crawl loop."""
    if not text.strip():
        return ""
    if not settings.bart_enabled:
        return _fallback_summary(text)
    try:
        return await asyncio.to_thread(_summarize_sync, text)
    except Exception as exc:
        log.warning("BART summarization unavailable; using extractive fallback: %s", exc)
        return _fallback_summary(text)
