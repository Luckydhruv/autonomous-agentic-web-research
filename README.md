# Autonomous Agentic Web Research & Crawling System

An AI-powered system that converts a natural-language research goal into an adaptive web-crawling strategy, extracts structured evidence, ranks results, and generates a research report.

---

## Features

- **Adaptive crawl loop** — reprioritises the URL queue in real time based on which required fields are still missing
- **LLM-assisted planning** — converts a plain-English objective into a structured research plan (topic, fields, search queries, stopping criteria)
- **Deterministic fallback** — planner, extractor, and reporter all work without any LLM
- **SSRF protection** — blocks private IPs, loopback, cloud metadata endpoints, non-HTTP schemes, and unsafe redirects
- **robots.txt compliance** and per-domain rate limiting
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
| HTTP | httpx (async) |
| Parsing | BeautifulSoup4 |
| LLM | Ollama (local) · OpenAI-compatible cloud (optional) · BART page summarizer (local) |
| Search | DuckDuckGo HTML scraping (default) · Serper API (optional) |
| Database | SQLite (default) · PostgreSQL (via `DATABASE_URL`) |

---

## Architecture

```
frontend (React + Vite)
    │  HTTP proxy → :8000
backend (FastAPI)
    ├── planner.py    NL objective → ResearchPlan
    ├── discovery.py  Search queries → candidate URLs
    ├── crawler.py    Safe HTTP fetcher
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
- *(Optional)* [Ollama](https://ollama.com) for local LLM
- Python package dependencies for the local BART model (`transformers` and `torch`); the model weights download and cache on first use

---

## Installation (Windows)

```bat
:: 1. Copy environment file
copy .env.example .env

:: 2. Install backend dependencies
cd backend
pip install -r requirements.txt
cd ..

:: 3. Install frontend dependencies
cd frontend
npm install
cd ..
```

---

## Running the App

Open **two separate terminals**.

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

The backend removes common page chrome, extracts topic-matched passages, ranks the pages, and summarizes the top-ranked source with `facebook/bart-large-cnn`. Long relevant passages are split into 900-token chunks and consolidated when needed. The report displays the selected source, relevant extracted text, and BART summary. Model files are cached locally; summarization does not require an API key or an expiring access token. The first run needs an internet connection to fetch model files. Configure `BART_ENABLED`, `BART_MODEL`, `BART_INPUT_TOKENS`, and `BART_SUMMARY_TOKENS` in `.env`; if model loading fails, the app keeps running and uses an extractive fallback.

### Serper search API (more reliable than DuckDuckGo scraping)

```
SEARCH_PROVIDER=serper
SERPER_API_KEY=your_key_here
```

---

## Key Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `OLLAMA_ENABLED` | `true` | Set `false` to skip Ollama |
| `OLLAMA_MODEL` | `llama3.2` | Any model pulled in Ollama |
| `SEARCH_PROVIDER` | `duckduckgo_html` | `duckduckgo_html` · `serper` · `none` |
| `DATABASE_URL` | `sqlite:///./research.db` | Use a PostgreSQL URL for production |
| `DEFAULT_MAX_PAGES` | `20` | Crawl budget per task |
| `DEFAULT_CRAWL_DEPTH` | `2` | Max link-following depth |
| `BART_ENABLED` | `true` | Use local BART to summarize each extracted page |
| `BART_MODEL` | `facebook/bart-large-cnn` | Hugging Face model identifier, cached locally after download |
| `BART_INPUT_TOKENS` | `900` | Per-chunk input budget, safely below BART's input limit |
| `BART_SUMMARY_TOKENS` | `100` | Maximum tokens for each page chunk summary |
| `EXTRA_BLOCKED_DOMAINS` | *(empty)* | Comma-separated extra SSRF blocklist |

> **Security:** Never commit `.env` or any file containing API keys. `.env` is listed in `.gitignore`.

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
│   ├── crawler.py · extractor.py · evaluator.py
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
├── start_backend.bat
├── start_frontend.bat
├── run_tests.bat
└── check.py            # smoke test (no server required)
```

---

## Known Limitations

- JavaScript-heavy single-page apps are not rendered. Playwright support exists (`PLAYWRIGHT_ENABLED=true`) but is not installed by default.
- The DuckDuckGo HTML search provider may return fewer results or be rate-limited under heavy use.
- Results reflect publicly available web content at crawl time; prices and availability may change.
- DNS rebinding is a theoretical SSRF edge case not fully mitigated at the HTTP transport layer.
