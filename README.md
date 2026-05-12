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
├── config.json             # All runtime configuration (URLs, tokens, paths)
├── config.example.json     # Reference example showing every supported field
├── manage.py               # CLI: reset-db, run-aggregation, run-status, etc.
│
├── app/                    # Application package
│   ├── __init__.py
│   ├── config.py           # Loads + validates config.json
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
│   │   ├── _aggregation_prompt.py   # Shared Prompt 1 helpers (template, anonymisation, NBSP cleanup)
│   │   ├── _highlights_prompt.py    # Shared Prompt 2 helpers (splice, strip, render)
│   │   ├── _status_prompt.py        # Shared Prompt 3 helpers (used by engine + standalone script)
│   │   ├── aggregation.py           # Prompt 1 → consolidated weekly report
│   │   ├── highlights.py            # Prompt 2 → week-over-week comparison spliced into Highlights
│   │   └── status.py                # Prompt 3 → project status JSON + DB persistence
│   │
│   ├── prompts/            # Versioned prompt templates (.txt files)
│   │   ├── weekly_aggregation_v1.txt
│   │   ├── highlights_comparison_v1.txt
│   │   ├── project_status_reasoning_v1.txt    # Preserved for audit per FR §A.6.1
│   │   └── project_status_reasoning_v2.txt    # Current — adds extra-context-pages support
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
│   ├── holidays_ist_2026.json
│   └── report_template_default.md
│
├── logs/                   # Runtime logs (JSONL); gitignored
│
├── scripts/                # One-off utilities
│   ├── test_status_prompt.py            # Standalone Prompt 3 end-to-end (Step 4)
│   ├── show_last_prompt3_result.py      # Print rationale + milestones from last prompt3 run
│   └── e2e_smoke.py                     # Full-pipeline OK/FAIL runbook (Step 12)
│
└── tests/                  # pytest tests (~190 total)
    ├── conftest.py
    ├── test_smoke.py                       # 5
    ├── test_clients.py                     # 32 — JIRA + Confluence + HTTP session
    ├── test_llm.py                         # 14 — Ollama + OpenAI-compatible + factory routing
    ├── test_registry.py                    # 22 — projects + engineers (StaticPool fixture)
    ├── test_aggregation_engine.py          # 10
    ├── test_aggregation_prompt.py          # 11 — anonymisation, NBSP cleanup, section names
    ├── test_highlights_engine.py           # 8
    ├── test_highlights_prompt.py           # 16 — splice, strip, regex, render
    ├── test_status_engine.py               # 11
    ├── test_scheduler.py                   # 27 — job registration (incl. reminders) + lifecycle + safe wrappers
    ├── test_notifications.py               # 12 — send + pre/post orchestration + mock JSONL
    ├── test_api.py                         # 25 — public REST API + admin endpoints (FastAPI TestClient)
    └── test_e2e.py                         #  4 — end-to-end pipeline integration (Step 12)
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

Edit `config.json` (a fully-populated reference is in `config.example.json`):

1. Set `jira.base_url` and `jira.token`
2. Set `confluence.base_url` and `confluence.token`
3. Set `llm.provider` to `ollama` or `openai` and the corresponding URL/model
4. Add your projects under the `projects` array (see `config.example.json` for the full shape)
5. Edit `data/engineer_project_mapping.json` to map engineers to projects
6. Edit `data/holidays_ist_2026.json` with your holiday calendar

---

## Run

```bash
# Reset the database (Phase 1 only — destructive; use during dev)
python manage.py reset-db

# Sync projects from config.json into the DB (also runs at server startup)
python manage.py sync-projects

# Start the API server (also auto-starts the APScheduler — see Scheduler section)
python manage.py serve
# OR equivalently:
uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --reload
```

---

## CLI command reference (`python manage.py <command>`)

Run `python manage.py --help` for the full list. Grouped by purpose:

### Server + DB
| Command | Purpose |
|---|---|
| `serve [--host H] [--port P] [--reload]` | Start FastAPI + scheduler (lifespan). |
| `reset-db` | Drop + recreate all tables (DEV ONLY — destructive). |
| `sync-projects` | Read `config.json` `projects` → upsert into DB. |
| `list-projects` | Print all projects in the DB. |
| `list-engineers [--project-code X]` | Print engineers from `data/engineer_project_mapping.json`. |
| `show-mapping [project_key]` | Diagnostic: see parsed engineer mapping + matches. |

### Source-system probes
| Command | Purpose |
|---|---|
| `whoami-jira` | Verify JIRA token (`/rest/api/2/myself`). |
| `whoami-confluence` | Verify Confluence token (`/rest/api/content?limit=1` — chosen to dodge per-endpoint rate-limit on `/space`/`/user/*`). |
| `confluence-probe` | Single-shot raw Confluence probe — print full response headers + body for diagnosing 429s without burning the retry budget. |
| `jira-search <key>` | Recent issues in a JIRA project (JQL printed). |
| `jira-snapshot <key>` | Counts/overdue/recent — same shape Status Engine consumes. |
| `jira-task <issue_key>` | Comments + worklogs of one issue. |
| `jira-activity <key>` | Engineer-grouped weekly activity — same shape Aggregation Engine consumes. |
| `fetch-confluence-page <url> [--kind milestones\|fr\|extra] [--full]` | Fetch + parse. `--full` prints untruncated content. |

### LLM
| Command | Purpose |
|---|---|
| `llm-ping [--prompt T] [--json-output]` | One short completion. |
| `llm-embed <text>` | One embedding. |

### Engines + Notifications (manual triggers — same code path the scheduler uses)
| Command | Purpose |
|---|---|
| `run-status <code>` | Status Engine: Confluence + JIRA + past reports → Prompt 3 → DB. |
| `run-aggregation <code> [--week-of YYYY-MM-DD] [--regenerate]` | Aggregation Engine: JIRA per-engineer activity → Prompt 1 → WeeklyReport. |
| `run-highlights <code> [--week-of YYYY-MM-DD]` | Highlights Engine: this+last week reports → Prompt 2 → splice into Highlights. |
| `run-reminders <code> [--type pre\|post] [--week-of YYYY-MM-DD]` | Reminder dispatch (mock-SMTP): `pre` to ALL engineers; `post` only to engineers missing JIRA activity. |
| `show-last-reminders [--hours N]` | Print recent mock-sent reminder emails from `logs/sent_emails.jsonl`. |

### Scheduler
| Command | Purpose |
|---|---|
| `scheduler-status` | List jobs that WOULD be registered (with next fire times). Does NOT start anything. Safe to run anytime. |
| `scheduler-runs [--hours N] [--errors-only] [--lines N]` | Show recent scheduler events from `logs/system.jsonl` — proof that jobs ACTUALLY FIRED. |

### Inspection (logs)
| Command | Purpose |
|---|---|
| `show-last-llm-call [--lines N] [--mode ollama\|openai] [--prompt-only] [--response-only]` | Print full LLM I/O from `logs/llm_prompts.jsonl`. |
| `show-last-external-calls [--source jira\|confluence] [--lines N] [--errors-only]` | Print recent JIRA/Confluence calls from `logs/external_calls.jsonl`. |

---

## Scheduler — how it works and how to verify

**APScheduler runs inside the FastAPI process** — it's started by `python manage.py serve` (lifespan startup) and stopped on Ctrl-C / shutdown. There is no separate cron/daemon to manage.

For each project in the DB, **four jobs** are registered:

1. **Status compute** — daily at `cfg.scheduler.daily_status_hour` (default **06:00 IST**). Or hourly. Or not at all (`recompute_cadence = "manual"` per-project).
2. **Weekly pipeline** — once per week at the project's `weekly_cutoff` + `weekly_aggregation_offset_minutes` (default **Mon 13:05 IST**). Runs Aggregation Engine first; on success runs Highlights Engine.
3. **Pre-cutoff reminder** — once per week at `weekly_cutoff − cfg.reminders.hours_before_cutoff` (default 24h, so **Sun 13:00 IST**). Sends a heads-up email to ALL engineers assigned to the project.
4. **Post-cutoff reminder** — once per week at `weekly_cutoff + cfg.reminders.hours_after_cutoff` (default 4h, so **Mon 17:00 IST**). Queries JIRA, sends a late-notification only to engineers who recorded no activity for the week.

Both job bodies wrap engine calls in try/except — engine failures get logged but never kill the scheduler thread. Misfires within 24h (configurable) still run when the process comes back up.

### Verify the scheduler is up

```bash
# In one terminal — start the server (scheduler starts with it)
python manage.py serve

# In another terminal — see what's scheduled and when it'll next fire
python manage.py scheduler-status
```

Sample output:
```
[OK] 2 job(s) registered for 1 project(s):
  Timezone:                Asia/Kolkata
  Daily status hour:       06:00
  Weekly aggregation offset: +5 min after cutoff
  Misfire grace:           86400s

  reminder-post:MAICTJ             next: 2026-05-11 17:00 IST
  reminder-pre:MAICTJ              next: 2026-05-10 13:00 IST
  status:MAICTJ                    next: 2026-05-11 06:00 IST
  weekly:MAICTJ                    next: 2026-05-11 13:05 IST
```

### Verify jobs actually fired (over time)

```bash
python manage.py scheduler-runs                       # last 48h, all events
python manage.py scheduler-runs --hours 24
python manage.py scheduler-runs --errors-only         # failures + crashes only
```

Each event is one line with the most informative fields: `overall_health`, `schedule_status`, `completion_pct`, `engineer_count`, `duration_seconds`, etc. If you see `scheduler_started` but no other events — jobs are scheduled but their fire time hasn't come up yet.

### Fire a job NOW without waiting

The CLI engine commands are the same code path the scheduler invokes:

```bash
python manage.py run-status MAICTJ                        # same as the daily status job
python manage.py run-aggregation MAICTJ                   # same as the weekly aggregation half
python manage.py run-highlights MAICTJ                    # same as the weekly highlights half
python manage.py run-reminders MAICTJ --type pre          # same as the pre-cutoff reminder job
python manage.py run-reminders MAICTJ --type post         # same as the post-cutoff reminder job
python manage.py show-last-reminders                      # see what would have been emailed
```

---

## REST API surface (Step 11)

When the server is running (`python manage.py serve`), the following endpoints are available. **No authentication in Phase 1** (per NFR — relies on office-network perimeter). Auto-generated OpenAPI docs at `http://127.0.0.1:8000/docs`.

### Public — projects, status, reports
| Method | Path | Returns |
|---|---|---|
| GET | `/health` | server + version + LLM provider + project count |
| GET | `/api/projects` | List all projects (summary view) |
| GET | `/api/projects/{code}` | Single project full detail |
| GET | `/api/projects/{code}/tasks` | Currently active JIRA tasks (ID + title + URL) |
| GET | `/api/projects/{code}/status` | Latest computed status (Green/Amber/Red, schedule, completion%, milestones, rationale, confidence) |
| GET | `/api/projects/{code}/status/history?limit=N` | Status-change timeline (newest first; only changes are recorded) |
| POST | `/api/projects/{code}/status/refresh` | Trigger immediate Status Engine recompute |
| GET | `/api/projects/{code}/reports?from=YYYY-MM-DD&to=YYYY-MM-DD&limit=N` | List weekly reports (summary; newest first) |
| GET | `/api/projects/{code}/reports/latest` | Most recent weekly report (full markdown) |
| GET | `/api/projects/{code}/reports/{week_of}` | Specific week's weekly report (full markdown) |
| POST | `/api/projects/{code}/reports/{week_of}/regenerate` | Trigger Aggregation Engine for that week |
| POST | `/api/projects/{code}/reports/{week_of}/highlights/refresh` | Trigger Highlights Engine for that week |

### Admin — read-only observability
| Method | Path | Returns |
|---|---|---|
| GET | `/admin/config` | Safe subset of runtime config (no tokens / API keys) |
| GET | `/admin/logs/ai-computes?project_code=&limit=N` | Recent LLM compute log entries |
| GET | `/admin/logs/reminders?project_code=&limit=N` | Recent mock-sent reminder emails |
| GET | `/admin/logs/sync?source=jira\|confluence&limit=N` | Recent JIRA/Confluence sync log entries |
| GET | `/admin/scheduler/jobs` | Currently registered APScheduler jobs (with next run times) |

### Quick examples

```bash
# List all projects
curl http://127.0.0.1:8000/api/projects

# Get current status of one project
curl http://127.0.0.1:8000/api/projects/MAICTJ/status

# Trigger an immediate status recompute
curl -X POST http://127.0.0.1:8000/api/projects/MAICTJ/status/refresh

# Get this Monday's weekly report
curl http://127.0.0.1:8000/api/projects/MAICTJ/reports/2026-05-04

# See what's scheduled
curl http://127.0.0.1:8000/admin/scheduler/jobs

# Recent AI compute history (most recent first)
curl http://127.0.0.1:8000/admin/logs/ai-computes?limit=10
```

The interactive Swagger UI at `/docs` exposes every endpoint with try-it-now forms — convenient for ad-hoc PGM-style exploration without building a UI.

---

## Diagnostic logs

All structured JSONL under `logs/`. Three categories:

| File | Always-on? | Inspect with |
|---|---|---|
| `logs/system.jsonl` | ✅ Always | (any text editor or `findstr`) — engine events, scheduler events, retry warnings, startup/shutdown |
| `logs/ai_compute.jsonl` + `AIComputeLog` DB table | ✅ Always | (DB query) — compact LLM audit trail (success/failure, mode, duration, 200-char response excerpt, prompt version) |
| `logs/llm_prompts.jsonl` | Gated by `config.logging.log_full_llm_prompts` (default `true`) | `show-last-llm-call` — FULL system + user prompts + raw response per LLM call |
| `logs/external_calls.jsonl` | Gated by `config.logging.log_full_external_calls` (default `true`) | `show-last-external-calls` — FULL JIRA/Confluence call (path, JQL/params, status, duration, result_summary) |
| _live stderr_ | Gated by `config.logging.echo_external_calls_to_stderr` (default `true`) | watch the terminal — every JIRA/Confluence call prints one line to STDERR as it fires (JQL / path / status / duration / result count) so you can see queries live without tailing the JSONL log |
| `logs/sent_emails.jsonl` + `ReminderLog` DB table | ✅ Always (mock mode) | `show-last-reminders` — every mock-sent reminder email (recipient, subject, body, type=pre/post_cutoff) |
| `logs/reminder.jsonl` | ✅ Always | (any text editor) — compact one-line per reminder send (knox_id, project, type, status) |

Both gated logs default ON during stabilisation. Once Phase 1 is stable, flip to `false` in `config.json` to save disk; the always-on layers continue providing operational telemetry. The stderr echo is independent of the JSONL log — you can keep the on-disk log off and still get the terminal echo (useful for `serve` if you want clean disk + live debug), or vice versa.

Sample stderr echo from a `python manage.py run-aggregation MAICTJ` run:
```
[CONFLUENCE] GET /rest/api/content  title=MAICTJ Milestones, spaceKey=DS, expand=body.storage  -> 200 in 0.41s  (result_count=1, size=1)
[JIRA] GET /rest/api/2/search  jql=project = MAICTJ AND updated >= '2026-05-04' AND labels NOT IN ('backfill')  -> 200 in 0.83s  (issue_count=12, total=12)
[JIRA] GET /rest/api/2/issue/MAICTJ-7/comment                                                   -> 200 in 0.29s  (comment_count=3)
[JIRA] GET /rest/api/2/issue/MAICTJ-7/worklog                                                   -> 200 in 0.18s  (worklog_count=1)
```

---

## End-to-end verification (Step 12)

Two layers — automated (CI) and manual (against your real environment):

### 1. Automated integration test (no external systems needed)

```bash
pytest tests/test_e2e.py -v
```

Wires the full pipeline (Aggregation → Highlights → Status → Reminders → API)
with mocked LLM/JIRA/Confluence and a real in-memory DB. Proves every
engine + the API surface fit together.

### 2. Manual smoke against your real environment

```bash
# Run the 10-step pipeline against ONE real project; prints OK/FAIL with timing.
python scripts/e2e_smoke.py --project-code MAICTJ
python scripts/e2e_smoke.py --project-code MAICTJ --week-of 2026-05-04

# Skip the prior-week aggregation if you want to test the first-week flow
python scripts/e2e_smoke.py --project-code MAICTJ --skip-prior-week

# Skip reminder dispatch (e.g. to avoid filling the mock email log)
python scripts/e2e_smoke.py --project-code MAICTJ --skip-reminders
```

The script exercises (in order): JIRA whoami, Confluence whoami, LLM
ping, registry sync, engineer-mapping resolution, prior-week
aggregation, current-week aggregation, highlights, status compute,
pre-cutoff reminders, post-cutoff reminders. Exit code 0 if every
step passes, 1 otherwise.

### 3. Verify the REST API end-to-end

After `e2e_smoke.py` passes:

```bash
python manage.py serve            # in one terminal
curl http://127.0.0.1:8000/api/projects/MAICTJ/status
curl http://127.0.0.1:8000/api/projects/MAICTJ/reports/latest
curl http://127.0.0.1:8000/admin/scheduler/jobs
```

Or open the interactive API explorer at `http://127.0.0.1:8000/docs`.

---

## Test

```bash
pytest -v                          # all ~220 tests
pytest tests/test_smoke.py -v      # smoke check only
pytest tests/test_scheduler.py -v  # one suite
pytest -k highlights -v            # filter by keyword
```

---

## Status (Phase 1 build progress)

| Step | Module(s) | Status |
|------|-----------|--------|
| 1 | Repo scaffold | ✅ |
| 2 | HTTP session + JIRA + Confluence clients | ✅ validated end-to-end against real JIRA DC + Confluence DC |
| 3 | LLM client (Ollama + OpenAI-compatible) | ✅ validated against internal openai gateway (chat); embeddings endpoint not exposed by gateway, not used by Phase 1 |
| 4 | Standalone Prompt 3 test script | ✅ validated end-to-end against a real project — schema PASS, asymmetric trust invariant held, rationale cites specific milestone names + dates |
| 5 | Project Registry + engineer mapping loader | ✅ — sync from config.json on startup; lookup helpers; 21 tests |
| 6 | Aggregation Engine (Prompt 1) | ✅ — `run_weekly_aggregation()` + per-engineer JIRA pull + Prompt 1 + WeeklyReport upsert with regenerate semantics; 10 tests with mocked LLM/JIRA |
| 7 | Highlights Engine (Prompt 2) | ✅ — `run_highlights()` + week-over-week comparison + splice into WeeklyReport's Highlights section; ~25 tests with mocked LLM |
| 8 | Status Engine (Prompt 3 wrapped) | ✅ — `run_status_compute()` + persistence; 11 tests with mocked LLM/JIRA/Confluence |
| 9 | Scheduler | ✅ — APScheduler in FastAPI lifespan; per-project daily status job + weekly aggregation→highlights pipeline (cutoff + offset); idempotent start/stop; misfire grace; scheduler-status CLI; ~20 tests |
| 10 | Notifications (mocked SMTP) | ✅ — `send_engineer_reminder()` writes to `logs/sent_emails.jsonl` + `ReminderLog` DB; orchestrators `run_pre_cutoff_reminders()` (all engineers) + `run_post_cutoff_reminders()` (only those missing JIRA activity); scheduler fires both per project per week (cutoff ± hours); `run-reminders` + `show-last-reminders` CLIs; ~25 tests |
| 11 | API routes | ✅ — Public REST API: `/api/projects` (list/detail/tasks), `/api/projects/{code}/status` (current/history/refresh), `/api/projects/{code}/reports` (list/latest/by-week/regenerate/highlights-refresh), `/admin/{config,logs/{ai-computes,reminders,sync},scheduler/jobs}`; ~25 tests via FastAPI TestClient |
| 12 | End-to-end test on one real project | ✅ — `tests/test_e2e.py` (4 integration tests wiring all engines + DB + API end-to-end with mocked LLM/JIRA/Confluence); `scripts/e2e_smoke.py` (10-step CLI runbook against real environment with [OK]/[FAIL] reporting) |
