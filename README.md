# Autonomous Web Research

A small research app: it searches the web through Crawl4AI, extracts relevant page text, summarizes it with the local BART checkpoint, and displays the report in readable sections.

## Backend layout

The runtime backend is five Python files:

- `backend/main.py` — FastAPI routes.
- `backend/research.py` — Crawl4AI search, crawling, extraction, and ranking.
- `backend/report.py` — BART summaries and report sections.
- `backend/database.py` — SQLite task storage.
- `backend/models.py` — settings and API/data models.

## Run locally

Install Python 3.11+, Node.js 18+, and the project dependencies. Crawl4AI uses Chromium directly; Docker and a separate crawler API service are not needed.

```bash
cp .env.example .env
python -m venv venv
venv/bin/pip install -r backend/requirements.txt
venv/bin/python -m playwright install chromium
npm --prefix frontend install
```

Run the app in two terminals:

```bash
./start_backend.sh
```

```bash
./start_frontend.sh
```

Open <http://localhost:5173>.

On Windows, use `start_backend.bat` and `start_frontend.bat`; install the Python requirements and Chromium with `python -m playwright install chromium` first.

## Reports

Research pages are ranked by topic overlap. BART summarizes relevant text in token-safe chunks. The final report is saved and shown as separate source and synthesis sections, each source linked to its original page. If the fine-tuned model files are not present, the summarizer falls back to `facebook/bart-large-cnn`; if model loading fails, it uses an extractive summary.

## Configuration

Copy `.env.example` to `.env`. `BART_MODEL=auto` selects the fine-tuned model files in the repository root. `BART_INPUT_TOKENS` controls the source-text chunk size. `BART_SUMMARY_TOKENS` controls each generated summary chunk. SQLite stores tasks in `backend/research.db` by default.
