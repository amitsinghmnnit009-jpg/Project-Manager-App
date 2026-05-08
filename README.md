# Project-Manager-App

AI-assisted project-management tool for the Project Group Manager (PGM).

This is the **Phase 1** implementation. Specs and decisions live in the parent
folder `d:\Project-Manager\` (PHASE1_FUNCTIONAL_REQUIREMENTS.md, NFR_PHASE1.md,
BACKEND_ARCHITECTURE_PHASE1.md, AI_PROMPTS_PHASE1.md, etc.).

---

## What this app does (Phase 1)

1. Reads engineers' weekly activity from JIRA Data Center (task comments + work-logs)
2. Reads each project's Milestones + Functional Requirements from a Confluence page
3. Uses AI (gpt-oss via Ollama or an OpenAI-compatible internal endpoint) to:
   - Aggregate raw engineer inputs into a consolidated weekly project report
   - Detect missed commitments / carried-over risks / newly raised risks (Highlights section)
   - Determine project health (Green/Amber/Red), schedule status, and completion %
4. Surfaces all of the above to the PGM via a REST API (UI deferred to a later phase)

---

## Folder layout

```
Project-Manager-App/
├── pyproject.toml          # Python project + dependencies
├── README.md               # This file
├── .gitignore
├── config.yaml             # All runtime configuration (URLs, tokens, paths)
├── manage.py               # CLI: reset-db, run-aggregation, run-status, etc.
│
├── app/                    # Application package
│   ├── __init__.py
│   ├── config.py           # Loads + validates config.yaml
│   ├── db.py               # SQLAlchemy engine + session factory
│   ├── models.py           # SQLAlchemy ORM tables
│   │
│   ├── llm/                # LLM client abstraction
│   │   ├── base.py         # Abstract interface
│   │   ├── ollama_client.py
│   │   └── openai_client.py
│   │
│   ├── clients/            # External REST integrations
│   │   ├── http_session.py # Shared requests session with 429-aware retry
│   │   ├── jira_client.py  # JIRA DC REST API
│   │   └── confluence_client.py
│   │
│   ├── registry/           # Project + engineer registries
│   │   ├── projects.py
│   │   └── engineers.py
│   │
│   ├── engines/            # The three AI-driven engines
│   │   ├── aggregation.py  # Prompt 1: weekly report generation
│   │   ├── highlights.py   # Prompt 2: week-over-week comparison
│   │   └── status.py       # Prompt 3: project status reasoning
│   │
│   ├── prompts/            # Versioned prompt templates (.txt files)
│   │   ├── weekly_aggregation_v1.txt
│   │   ├── highlights_comparison_v1.txt
│   │   └── project_status_reasoning_v1.txt
│   │
│   ├── api/                # FastAPI routes
│   │   ├── main.py         # FastAPI app entrypoint
│   │   ├── projects.py
│   │   ├── reports.py
│   │   ├── status.py
│   │   └── admin.py
│   │
│   ├── scheduler.py        # APScheduler — daily status, weekly aggregation
│   ├── notifications.py    # Email reminders (mocked in Phase 1)
│   │
│   └── utils/
│       ├── logging.py      # Structured JSONL logger
│       └── dates.py        # IST week calculations, cutoff resolution
│
├── data/                   # Editable data files
│   ├── engineer_project_mapping.json
│   ├── holidays_ist_2026.yaml
│   └── report_template_default.md
│
├── logs/                   # Runtime logs (JSONL); gitignored
│
├── scripts/                # One-off utilities
│   └── test_status_prompt.py  # Standalone Prompt 3 test (Step 4)
│
└── tests/                  # pytest tests
    ├── conftest.py
    └── test_smoke.py
```

---

## Setup

```bash
cd Project-Manager-App
python -m venv .venv
.venv\Scripts\activate         # Windows
# or:  source .venv/bin/activate    # Ubuntu

pip install -e ".[dev]"
```

---

## Configure

Edit `config.yaml`:

1. Set `jira.base_url` and `jira.token`
2. Set `confluence.base_url` and `confluence.token`
3. Set `llm.provider` to `ollama` or `openai` and the corresponding URL/model
4. Add your projects under the `projects:` list (see example in the file)
5. Edit `data/engineer_project_mapping.json` to map engineers to projects
6. Edit `data/holidays_ist_2026.yaml` with your holiday calendar

---

## Run

```bash
# Reset the database (Phase 1 only — destructive; use during dev)
python manage.py reset-db

# Start the API server
uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --reload

# OR run via manage.py
python manage.py serve
```

---

## Test

```bash
pytest                        # all tests
pytest tests/test_smoke.py    # smoke check
```

---

## Status (Phase 1 build progress)

| Step | Module(s) | Status |
|------|-----------|--------|
| 1 | Repo scaffold | ✅ |
| 2 | HTTP session + JIRA + Confluence clients | ✅ validated end-to-end against real JIRA DC + Confluence DC |
| 3 | LLM client (Ollama + OpenAI-compatible) | ✅ validated against internal openai gateway (chat); embeddings endpoint not exposed by gateway, not used by Phase 1 |
| 4 | Standalone Prompt 3 test script | ✅ implemented; awaiting first run against a real Confluence-filled project |
| 5 | Project Registry + engineer mapping loader | ⬜ |
| 6 | Aggregation Engine (Prompt 1) | ⬜ |
| 7 | Highlights Engine (Prompt 2) | ⬜ |
| 8 | Status Engine (Prompt 3 wrapped) | ⬜ |
| 9 | Scheduler | ⬜ |
| 10 | Notifications (mocked SMTP) | ⬜ |
| 11 | API routes | ⬜ |
| 12 | End-to-end test on one real project | ⬜ |
