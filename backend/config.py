"""
config.py – centralised settings loaded from environment / .env file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_env: str = "development"
    app_port: int = 8000
    frontend_url: str = "http://localhost:5173"

    # Database
    database_url: str = "sqlite+aiosqlite:///./research.db"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_enabled: bool = True

    # Cloud LLM (OpenAI-compatible)
    cloud_llm_enabled: bool = False
    cloud_llm_base_url: str = "https://api.openai.com/v1"
    cloud_llm_api_key: str = ""
    cloud_llm_model: str = "gpt-4o-mini"

    # Search provider
    search_provider: str = "duckduckgo_html"
    serper_api_key: str = ""

    # Crawl defaults
    default_max_pages: int = 20
    default_crawl_depth: int = 2
    default_request_timeout: int = 15
    default_max_response_size_kb: int = 2048
    default_rate_limit_delay: float = 1.5

    # "auto" prefers the fine-tuned checkpoint in the repository root; set this
    # to a Hugging Face ID or local path to override it.
    bart_enabled: bool = True
    bart_model: str = "auto"
    bart_input_tokens: int = 900
    bart_summary_tokens: int = 100

    # Security
    extra_blocked_domains: str = ""

    # Playwright
    playwright_enabled: bool = False

    @property
    def blocked_domains(self) -> list[str]:
        """Returns combined list of built-in + user-configured blocked domains."""
        built_in = [
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "::1",
            "169.254.169.254",   # AWS metadata
            "metadata.google.internal",
        ]
        extra = [
            d.strip()
            for d in self.extra_blocked_domains.split(",")
            if d.strip()
        ]
        return built_in + extra


settings = Settings()
