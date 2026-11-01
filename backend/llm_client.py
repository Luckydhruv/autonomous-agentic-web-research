"""
llm_client.py – Thin wrapper around Ollama (primary) and an optional
OpenAI-compatible cloud LLM (fallback).  Returns plain-text or JSON
responses.  Never raises on LLM unavailability – returns None instead so
callers can apply their own deterministic fallback.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from config import settings

log = logging.getLogger(__name__)

_OLLAMA_TIMEOUT = 60  # seconds


async def _call_ollama(prompt: str, json_mode: bool = False) -> Optional[str]:
    """Send a prompt to Ollama and return the response text, or None on failure."""
    if not settings.ollama_enabled:
        return None
    try:
        payload: dict[str, Any] = {
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
        }
        if json_mode:
            payload["format"] = "json"

        async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return None
            response_text = data.get("response")
            return response_text.strip() if isinstance(response_text, str) else None
    except Exception as exc:
        log.warning("Ollama unavailable: %s", exc)
        return None


async def _call_cloud_llm(prompt: str, json_mode: bool = False) -> Optional[str]:
    """Call an OpenAI-compatible cloud LLM. Returns None if disabled or on error."""
    if not settings.cloud_llm_enabled or not settings.cloud_llm_api_key:
        return None
    try:
        messages = [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {
            "model": settings.cloud_llm_model,
            "messages": messages,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {settings.cloud_llm_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.cloud_llm_base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        log.warning("Cloud LLM unavailable: %s", exc)
        return None


async def generate(prompt: str, json_mode: bool = False) -> Optional[str]:
    """
    Try Ollama first, then cloud LLM.
    Returns raw text (or JSON string when json_mode=True), or None if both unavailable.
    """
    result = await _call_ollama(prompt, json_mode=json_mode)
    if result:
        return result
    result = await _call_cloud_llm(prompt, json_mode=json_mode)
    return result


async def generate_json(prompt: str) -> Optional[dict]:
    """Convenience wrapper that returns a parsed dict or None."""
    raw = await generate(prompt, json_mode=True)
    if not raw:
        return None
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last fence lines
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract first JSON object from the text
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                pass
    log.warning("LLM returned non-JSON: %s", raw[:200])
    return None
