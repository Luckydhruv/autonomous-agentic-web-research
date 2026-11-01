"""Token-aware, local BART page summarization with a no-token fallback."""
from __future__ import annotations

import asyncio
import logging
import re
from functools import lru_cache
from pathlib import Path

from config import settings

log = logging.getLogger(__name__)


def _model_source() -> str:
    """Resolve the configured model, preferring the bundled fine-tuned weights."""
    configured = settings.bart_model.strip()
    if configured.lower() != "auto":
        return configured

    checkpoint_dir = Path(__file__).resolve().parent.parent
    required_files = ("config.json", "model.safetensors", "tokenizer.json")
    if all((checkpoint_dir / filename).is_file() for filename in required_files):
        return str(checkpoint_dir)

    # A clean clone may not contain the large fine-tuned weight file. Preserve
    # the previous behavior in that case, with Hugging Face's persistent cache.
    return "facebook/bart-large-cnn"


@lru_cache(maxsize=1)
def _load_model():
    # Lazy loading keeps startup lightweight; Hugging Face caches weights locally.
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    model_source = _model_source()
    is_local_checkpoint = Path(model_source).is_dir()
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_source, local_files_only=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_source, local_files_only=True)
    except OSError:
        if is_local_checkpoint:
            raise
        # First use of a remote model downloads it into Hugging Face's cache.
        tokenizer = AutoTokenizer.from_pretrained(model_source)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_source)
    model.eval()
    log.info("Loaded BART summarizer from %s", model_source)
    return tokenizer, model


def _fallback_summary(text: str, max_chars: int = 1800) -> str:
    """Return a compact sentence-based summary if model weights are unavailable."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    selected = [s.strip() for s in sentences if len(s.strip()) > 35][:8]
    if not selected and text.strip():
        selected = [text.strip()]
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
        min_tokens = min(30, max(8, len(chunk_ids) // 10))
        with torch.inference_mode():
            summary_ids = model.generate(
                **inputs,
                max_new_tokens=settings.bart_summary_tokens,
                min_length=min_tokens,
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
        combined_tokens = len(tokenizer.encode(combined, add_special_tokens=False))
        with torch.inference_mode():
            summary_ids = model.generate(
                **inputs,
                max_new_tokens=220,
                min_length=min(30, max(8, combined_tokens // 10)),
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
    # BART tends to expand very short inputs into generic prose. Keep brief facts
    # verbatim so statistics, dates, and source wording are not distorted.
    if len(text.split()) < 40:
        return _fallback_summary(text)
    if not settings.bart_enabled:
        return _fallback_summary(text)
    try:
        return await asyncio.to_thread(_summarize_sync, text)
    except Exception as exc:
        log.warning("BART summarization unavailable; using extractive fallback: %s", exc)
        return _fallback_summary(text)
