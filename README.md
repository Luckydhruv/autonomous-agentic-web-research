# Autonomous Web Research with Crawl4AI

An AI-powered research app that turns a natural-language objective into search queries, uses the local Crawl4AI API to read search results and crawl selected pages, extracts evidence, ranks sources, and generates a report.

---

## Features

- **Crawl4AI API crawling** — search-results pages and source pages are fetched through a self-hosted Crawl4AI Docker API
- **Adaptive crawl loop** — prioritises URLs based on relevance and still-missing fields
- **LLM-assisted planning** — converts a plain-English objective into a structured research plan (topic, fields, search queries, stopping criteria)
- **Deterministic fallback** — planner, extractor, and reporter all work without any LLM
- **Robots-aware Crawl4AI crawling** — the local service handles browser rendering, page cleanup, and robots checks
- **Weighted ranking** — scores each result on relevance, source reliability, freshness, completeness, and constraint match
- **Structured report** — executive summary, key findings, comparison table, crawl statistics, and confidence score
- **Relevant source summary with BART** — filters boilerplate, ranks topic-matched passages, summarizes the best source locally in token-safe chunks, and shows both the extracted text and BART summary in the report
- **Live progress** — frontend polls progress, sources, and ranked results while the agent is running
- **Cancel support** — any running task can be cancelled from the UI

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, Vite, Tailwind CSS, React Router |
| Backend | Python 3.11+, FastAPI, SQLAlchemy (async), aiosqlite |
| Crawling | Self-hosted Crawl4AI Docker API (`/crawl`) |
| HTTP | httpx (async), used to call the local Crawl4AI API |
| Parsing | BeautifulSoup4 for Markdown returned by Crawl4AI |
| LLM | Ollama (local) · OpenAI-compatible cloud (optional) · BART page summarizer (local) |
| Search discovery | Search-result pages are fetched through Crawl4AI; no search-provider SDK |
| Database | SQLite (default) · PostgreSQL (via `DATABASE_URL`) |

---

## Architecture

```
frontend (React + Vite)
    │  HTTP proxy → :8000
backend (FastAPI)
    ├── planner.py    NL objective → ResearchPlan
    ├── crawl4ai_client.py  Authenticated local API calls
    ├── discovery.py  Crawl search pages → candidate URLs
    ├── crawler.py    Crawl4AI source-page adapter
    ├── extractor.py  HTML → ExtractedRecord
    ├── evaluator.py  Relevance · coverage · duplicates · stop criteria
    ├── ranking.py    Weighted scoring
    ├── reporter.py   Report generation
    ├── agent.py      Adaptive crawl-loop controller
    ├── database.py   SQLAlchemy async ORM
    └── schemas.py    Pydantic models
```

---

## Prerequisites

- Python 3.11 or later
- Node.js 18 or later
- Docker Desktop or Docker Engine for the Crawl4AI service
- *(Optional)* [Ollama](https://ollama.com) for local LLM
- Python package dependencies for local BART (`transformers` and `torch`)

---

## Installation

```bat
:: 1. Copy environment file
copy .env.example .env

:: 2. Start local Crawl4AI (creates a persistent token in .env)
start_crawl4ai.bat

:: 3. Install backend dependencies
cd backend
pip install -r requirements.txt
cd ..

:: 4. Install frontend dependencies
cd frontend
npm install
cd ..
```

---

## Running the App

Start Docker and run `start_crawl4ai.bat` first. It launches Crawl4AI 0.9.2 on port 11235, creates a persistent local API token in `.env`, and reuses the named container on later starts. On Linux/macOS, use `./start_crawl4ai.sh`.

Then open **two separate terminals** for the app.

**Terminal 1 – backend** (port 8000):
```bat
start_backend.bat
```
or manually:
```bat
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 – frontend** (port 5173):
```bat
start_frontend.bat
```
or manually:
```bat
cd frontend
npm run dev
```

Then open **http://localhost:5173**.

---

## Optional Integrations

### Ollama (local LLM — improves planning, extraction, and report quality)

1. Install Ollama from https://ollama.com
2. Pull a model: `ollama pull llama3.2`
3. In `.env`: `OLLAMA_ENABLED=true`, `OLLAMA_MODEL=llama3.2`

The system falls back to deterministic keyword-based logic when Ollama is unavailable — no features are disabled.

### Cloud LLM (OpenAI-compatible)

```
CLOUD_LLM_ENABLED=true
CLOUD_LLM_BASE_URL=https://api.openai.com/v1
CLOUD_LLM_API_KEY=your_key_here   # never commit this
CLOUD_LLM_MODEL=gpt-4o-mini
```

### Local BART page summarization

The backend removes common page chrome, extracts topic-matched passages, ranks the pages, and summarizes the top-ranked source with the fine-tuned BART checkpoint placed in the repository root. With `BART_MODEL=auto` (the default), it loads the local `config.json`, `model.safetensors`, and tokenizer files, so no API key or expiring access token is needed. Long relevant passages are split into 900-token chunks and consolidated when needed. The report displays the selected source, relevant extracted text, and BART summary. If the checkpoint is absent in a clean clone, the app falls back to `facebook/bart-large-cnn` and caches it on first use. Configure `BART_ENABLED`, `BART_MODEL`, `BART_INPUT_TOKENS`, and `BART_SUMMARY_TOKENS` in `.env`; an explicit model ID or local path in `BART_MODEL` overrides automatic selection. If model loading fails, the app keeps running and uses an extractive fallback.


---

## Key Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `OLLAMA_ENABLED` | `true` | Set `false` to skip Ollama |
| `OLLAMA_MODEL` | `llama3.2` | Any model pulled in Ollama |
| `CRAWL4AI_API_BASE` | `http://localhost:11235` | Local Crawl4AI Docker API address |
| `CRAWL4AI_API_TOKEN` | generated by `start_crawl4ai` | Persistent local bearer token; keep it in `.env` |
| `DATABASE_URL` | `sqlite:///./research.db` | Use a PostgreSQL URL for production |
| `DEFAULT_MAX_PAGES` | `20` | Crawl budget per task |
| `DEFAULT_CRAWL_DEPTH` | `2` | Max link-following depth |
| `BART_ENABLED` | `true` | Use local BART to summarize each extracted page |
| `BART_MODEL` | `auto` | Prefer the fine-tuned repo-root checkpoint; set a Hugging Face model ID or local path to override |
| `BART_INPUT_TOKENS` | `900` | Per-chunk input budget, safely below BART's input limit |
| `BART_SUMMARY_TOKENS` | `100` | Maximum tokens for each page chunk summary |

> **Security:** Never commit `.env` or any file containing API keys or local API tokens. `.env` is listed in `.gitignore`.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/config` | Safe runtime config (no secrets) |
| `POST` | `/api/tasks` | Create a task and start the agent |
| `GET` | `/api/tasks` | List all tasks |
| `GET` | `/api/tasks/{id}` | Get a single task |
| `POST` | `/api/tasks/{id}/cancel` | Cancel a running task |
| `GET` | `/api/tasks/{id}/progress` | Live progress (pages, records, missing fields) |
| `GET` | `/api/tasks/{id}/sources` | Visited URLs and extracted records |
| `GET` | `/api/tasks/{id}/report` | Final ranked results and report |

**Create task** request body:
```json
{ "objective": "Find the best gaming laptops under ₹80,000", "max_pages": 20, "crawl_depth": 2 }
```
Constraints: `objective` 10–2000 chars · `max_pages` 3–100 · `crawl_depth` 1–5.

---

## Running Tests

```bat
cd backend
python -m pytest tests/ -v
```

91 tests across `test_api`, `test_crawler`, `test_discovery`, `test_evaluator`, `test_extractor`, `test_planner`, `test_ranking`, `test_reporter`.

Quick smoke test (no server needed):
```bat
python check.py
```

---

## Project Structure

```
AI/
├── backend/
│   ├── main.py · agent.py · planner.py · discovery.py
│   ├── crawl4ai_client.py · crawler.py · extractor.py
│   ├── evaluator.py
│   ├── ranking.py · reporter.py · database.py
│   ├── llm_client.py · config.py · schemas.py
│   ├── requirements.txt
│   └── tests/          # 91 pytest tests
├── frontend/
│   └── src/
│       ├── App.jsx · api.js · main.jsx
│       ├── pages/      Dashboard.jsx · TaskDetail.jsx
│       └── components/ ResearchForm · StatusBadge · ProgressBar · Icons
├── .env.example
├── start_crawl4ai.bat · start_crawl4ai.sh
├── start_backend.bat
├── start_frontend.bat
├── run_tests.bat
└── check.py            # smoke test (no server required)
```

---

## Crawl4AI integration

The app calls the self-hosted Crawl4AI server at `http://localhost:11235` using a bearer token. Crawl4AI local provides page crawling, not a dedicated web-search endpoint, so the app asks Crawl4AI to fetch public search-result pages and then queues relevant result links. Source-page fetches and link extraction also go through Crawl4AI. The service needs Docker and a local token; it does not need a Crawl4AI Cloud account or expiring cloud API key.

## Known Limitations

- New research tasks require the local Crawl4AI service to be running with a matching token.
- Search pages can still be rate-limited or change their result markup; Crawl4AI fetches and renders them while the app filters links for topic relevance.
- Results reflect publicly available web content at crawl time; prices and availability may change.
