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

    # Local Crawl4AI Python service (search is handled by the app; scraping
    # goes through the REST wrapper in crawl4ai_server.py).
    crawl4ai_api_base: str = "http://localhost:11235"
    crawl4ai_api_token: str = ""

    # Optional local/cloud LLMs support planning and structured extraction.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_enabled: bool = True
    cloud_llm_enabled: bool = False
    cloud_llm_base_url: str = "https://api.openai.com/v1"
    cloud_llm_api_key: str = ""
    cloud_llm_model: str = "gpt-4o-mini"

    # Local BART remains the report summarizer; Crawl4AI handles all search/fetch.
    bart_enabled: bool = True
    bart_model: str = "auto"
    bart_input_tokens: int = 900
    bart_summary_tokens: int = 100

    # Crawl defaults
    default_max_pages: int = 20
    default_crawl_depth: int = 2


settings = Settings()
