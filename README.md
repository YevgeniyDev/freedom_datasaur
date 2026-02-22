# FREEDOM_DATASAUR — FIRE Challenge
Ticket Enrichment • Routing • Fair Manager Assignment • Dashboard UI

---

## 1) Project, Purpose, Goals

This repository implements an end-to-end prototype for the FIRE Challenge:
a system that takes incoming customer tickets, enriches them with AI, routes them
to the correct office and eligible managers, and assigns tickets fairly using a
load-aware Round Robin algorithm.

Core goals:
- Automatically enrich tickets (category, urgency, sentiment, language, summary).
- Enforce hard routing constraints (VIP-only managers, “Смена данных” → chief).
- Assign fairly and deterministically (top-2 lowest load + round robin).
- Provide transparent reasoning (decision trace per assignment).
- Export results to a single CSV for evaluation.
- Provide a lightweight web UI to search tickets and view dashboard stats.

---

## 2) Technology Used, Dependencies, Setup Requirements

### Backend
- Python 3.11+
- FastAPI (API server)
- SQLAlchemy + Alembic (PostgreSQL ORM & migrations)
- PostgreSQL 16 (Docker container)
- rapidfuzz (string matching)
- fastText language detection model: lid.176.bin (bundled in repo)
- Optional OCR: pytesseract + PIL (for attachments)
- Ollama (LLM inference endpoint) used for AI enrichment

### Frontend
- Static HTML/CSS/JS (no build tools required)
- Chart.js (via CDN) for dashboards and plots

### Infrastructure / Runtime
- Docker + Docker Compose

### Requirements
- Docker Desktop installed and running
- Ports available:
  - 8000 (backend)
  - 55432 (postgres, if mapped)
  - 5173 (frontend static server, optional)
- Ollama running (recommended):
  - Windows/macOS: usually http://localhost:11434
  - Backend container must be able to reach it

---

## 3) Repository Structure

FREEDOM_DATASAUR/
├── backend/
│   ├── alembic/                 # migrations
│   ├── app/
│   │   ├── ai/                  # enrichment pipeline
│   │   │   ├── models/lid.176.bin
│   │   │   ├── enrich.py        # AI enrichment + guardrails + OCR
│   │   │   ├── lang_detect.py   # fastText language detection
│   │   │   ├── llm_client.py    # Ollama JSON chat client
│   │   │   ├── prompts.py       # prompt + format constraints
│   │   │   └── schema.py        # EnrichmentOut pydantic schema
│   │   ├── routing/
│   │   │   ├── rules.py         # hard constraints (VIP/chief/lang)
│   │   │   ├── allocator.py     # load-aware top2 + round robin
│   │   │   └── trace.py         # decision trace builder
│   │   ├── db/
│   │   │   ├── models.py        # Ticket, TicketAI, BusinessUnit, Manager, Assignment, RRState
│   │   │   └── session.py       # DB session factory
│   │   └── main.py              # FastAPI routes: lookup, list, stats, offices
│   ├── Dockerfile
│   ├── alembic.ini
│   └── requirements.txt
├── data/
│   ├── tickets.csv
│   ├── managers.csv
│   └── business_units.csv
├── frontend/
│   ├── index.html               # dashboard UI + charts
│   ├── app.js                   # API calls, filters, charts
│   └── main.css                 # styling
├── scripts/
│   ├── seed_db.py               # load CSVs into DB (truncates & reseeds)
│   └── run_batch.py             # enrich + route + assign + traces
├── docker-compose.yml
├── .env.example
└── README.md

---

## 4) Quick Setup Guide (Commands)

### A) Start everything (Docker)
From repo root:

1) Build + start containers:
   docker compose up --build -d

2) Seed DB (truncates and reloads CSVs):
   docker exec -it fire_backend bash -lc "python /app/scripts/seed_db.py"

3) Run batch enrichment + routing + assignment:
   docker exec -it fire_backend bash -lc "python /app/scripts/run_batch.py"

4) (Optional) Export results CSV (inside container):
   docker exec -it fire_backend bash -lc "python - << 'PY'
import os, pandas as pd
from sqlalchemy import create_engine, text
e = create_engine(os.environ['DATABASE_URL'])
q = '''
select
  t.client_guid,
  t.segment,
  t.country, t.region, t.city, t.street, t.house,
  t.description,
  t.attachment_path,
  ai.type_category,
  ai.sentiment,
  ai.urgency,
  ai.language as final_language,
  ai.needs_review,
  ai.summary,
  bu.office_name as assigned_office,
  m.full_name as assigned_manager,
  m.position as manager_position,
  m.skills as manager_skills,
  a.assigned_at
from tickets t
left join ticket_ai ai on ai.ticket_id = t.id
left join assignments a on a.ticket_id = t.id
left join managers m on m.id = a.manager_id
left join business_units bu on bu.id = a.business_unit_id
order by a.assigned_at nulls last, t.client_guid
'''
df = pd.read_sql_query(text(q), e)
df.to_csv('/app/data/results.csv', index=False, encoding='utf-8-sig')
print('Wrote /app/data/results.csv rows:', len(df))
PY"

5) Copy CSV to host:
   Windows PowerShell:
     docker cp fire_backend:/app/data/results.csv .\results.csv
   macOS/Linux:
     docker cp fire_backend:/app/data/results.csv ./results.csv

### B) Run the frontend dashboard
The frontend is static files.

From repo root:
  cd frontend
  python -m http.server 5173

Open:
  http://localhost:5173

Backend URL should be:
  http://localhost:8000

---

## 5) Backend & Frontend Explanation

### Backend flow (batch)
scripts/run_batch.py performs:
1) Load tickets, offices, managers from DB.
2) For each ticket without an Assignment:
   - enrich_ticket() runs AI enrichment (LLM + fastText + OCR optional).
   - apply deterministic guardrails:
       - prevent false "Спам" on tax/account support
       - prevent hallucination on low-info texts (e.g. "Help") by forcing needs_review
   - compute hard constraints (VIP/chief/lang).
   - choose office:
       - improved region-first logic routes villages to the correct regional office
       - fallback to city match or deterministic fallback
   - filter eligible managers in that office.
   - if no eligible managers, search other offices deterministically.
   - allocate manager:
       - choose top-2 lowest effective load
       - round-robin within that pair using RRState row lock
   - store Assignment with decision_trace.

### FastAPI API
backend/app/main.py exposes:
- GET /api/tickets/{guid}        # ticket details + enrichment + assignment
- GET /api/tickets              # list tickets with filters + pagination
- GET /api/offices              # offices dropdown
- GET /api/stats                # aggregates for charts (KPIs, distributions)

### Frontend dashboard
frontend/index.html + app.js provides:
- KPI cards from /api/stats
- filters (office/segment/category/lang/review/urgency/search)
- paginated table from /api/tickets
- click a GUID to load full detail from /api/tickets/{guid}
- charts rendered client-side using Chart.js:
  - tickets by category
  - tickets by office
  - urgency histogram
  - language distribution

---

## 6) Possible Problems & Troubleshooting

### 1) Frontend says “Cannot reach backend”
Check:
- http://localhost:8000/docs opens in browser
- container ports:
  docker ps
  docker port fire_backend

### 2) Endpoint /api/tickets not found
Make sure you rebuilt after editing backend:
  docker compose up --build -d

Then verify:
  http://localhost:8000/docs

### 3) “Assigned: 0, Skipped: N”
This happens when run_batch.py sees existing assignments and skips them.
If you changed enrichment rules and want to recompute, reseed:
  docker exec -it fire_backend bash -lc "python /app/scripts/seed_db.py"
  docker exec -it fire_backend bash -lc "python /app/scripts/run_batch.py"

### 4) LLM/Ollama not reachable
If backend is inside Docker and Ollama runs on host:
- Windows/macOS often supports host.docker.internal
Set in .env:
  OLLAMA_BASE_URL=http://host.docker.internal:11434

Verify from container:
  docker exec -it fire_backend bash -lc "curl -s http://host.docker.internal:11434/api/tags | head"

### 5) fastText model missing
Ensure file exists:
  backend/app/ai/models/lid.176.bin

Or set:
  FASTTEXT_LID_PATH=/app/backend/app/ai/models/lid.176.bin

### 6) OCR does nothing
OCR requires Tesseract runtime inside container. If not installed, enrich.py will safely ignore OCR.
(For hackathon demo, OCR is optional.)

### 7) CORS blocked in browser
main.py enables permissive CORS for hackathon ("*").
If you tighten CORS, add your frontend origin.

---

## 7) Limitations

- LLM classification can be wrong on edge cases; mitigated by rule guardrails, but not perfect.
- If ticket text is extremely short (“Help”), enrichment is forced into “needs_review” and safe defaults.
- Office routing is deterministic and region-first; without full coverage metadata or a settlement gazetteer,
  it cannot always uniquely choose the “nearest” office when multiple offices exist in one region.
- OCR quality depends on image clarity and installed language packs.
- Batch pipeline currently does not re-enrich or reassign already processed tickets unless you reseed.
- Chart stats are computed from DB aggregates; if data volume becomes huge, indexes and caching would be needed.

---

## 8) Further Improvements Possible

### Routing & Geo
- Add explicit office coverage metadata (regions/districts) to business_units.csv.
- Add district extraction (район/аудан) and use it to disambiguate office selection.
- Add a settlement→region cache (small offline gazetteer) to handle missing region fields.
- Store routing confidence and top-k office candidates in decision_trace.

### AI Enrichment Quality
- Add few-shot examples to prompts for frequent categories.
- Add a “low-information classifier” before calling the LLM to reduce cost and hallucinations.
- Add confidence-based needs_review thresholds (e.g., low type confidence => review).

### Operations / UI
- Add bulk actions: mark resolved, reassign, escalate, export filtered CSV.
- Add “AI assistant” for analytics:
  - natural language → safe JSON intent → validated aggregate queries
  - can answer questions like “How many Priority unassigned in Turkestan?”
- Add manager load visualization and fairness metrics.

### Engineering
- Add CLI flags/env vars for:
  - rerun enrichment only
  - rerun assignments
  - export CSV
- Add indexes on tickets.client_guid, assignments.ticket_id, ticket_ai.ticket_id for performance.
- Add unit tests for rule overrides and routing constraints.

---