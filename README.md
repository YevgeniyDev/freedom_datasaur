# FREEDOM_DATASAUR — FIRE Challenge (Ticket Enrichment + Routing + Assignment)

A lightweight end-to-end system that:

1. ingests incoming tickets,
2. enriches them with AI (category, urgency, sentiment, language, summary, optional OCR),
3. routes them to the correct office / manager using business rules + manager skills,
4. assigns fairly using Round-Robin with load-balancing,
5. exports a single `results.csv`,
6. provides a tiny frontend to lookup a ticket by its GUID.

---

## Repository Structure

- `backend/`
  - `app/`
    - `ai/` — language detection + LLM enrichment
      - `models/lid.176.bin` — fastText language id model
      - `enrich.py`, `lang_detect.py`, `llm_client.py`, `prompts.py`, `schema.py`
    - `routing/` — rules + allocator + trace
      - `rules.py`, `allocator.py`, `trace.py`
    - `db/` — SQLAlchemy session/models
    - `main.py` — FastAPI entrypoint
  - `alembic/` — DB migrations
  - `Dockerfile`
- `data/`
  - `tickets.csv`
  - `business_units.csv`
  - `managers.csv`
  - (optional) `results.csv` output
- `frontend/`
  - `index.html`, `app.js`, `main.css`
- `scripts/`
  - `seed_db.py` — load CSVs into DB
  - `run_batch.py` — run enrichment + routing + assignment (writes to DB)

---

## Prerequisites

- Docker + Docker Compose
- (For AI enrichment) an LLM endpoint:
  - default: Ollama on host
- fastText language model file:
  - must exist at: `backend/app/ai/models/lid.176.bin`

---

## Environment Variables

See `.env.example`. Common ones:

- `DATABASE_URL` (used inside backend container)
- `OLLAMA_BASE_URL` or similar (depending on `llm_client.py`)
- `FASTTEXT_LID_PATH` (optional override)
  - default expects: `/app/backend/app/ai/models/lid.176.bin`

---

## Quick Start (One Command End-to-End + Export CSV)

From repo root:

1. Start services:

docker compose up --build -d

2. Run migrations, seed DB, process tickets, export results:

docker exec -it fire_backend bash -lc "
cd /app/backend &&
alembic upgrade head &&
python /app/scripts/seed_db.py &&
python /app/scripts/run_batch.py &&
python - << 'PY'
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
PY
"

3. Copy CSV to host:

Windows PowerShell:
docker cp fire_backend:/app/data/results.csv .\results.csv

macOS/Linux:
docker cp fire_backend:/app/data/results.csv ./results.csv

---

## Debug / Verification

Check backend docs:
http://localhost:8000/docs

Check logs:
docker logs fire_backend --tail 100

---

## Ticket Lookup API (GUID → Assignment + Enrichment)

To support the frontend lookup, the backend provides:

GET /api/tickets/{client_guid}

Example:
http://localhost:8000/api/tickets/fe44694a-10ed-f011-8406-0022481ba5f0

If you see:

- 404 Not Found (endpoint): route is not registered (fix `backend/app/main.py`)
- 404 with JSON detail: GUID not in DB (pipeline not run or GUID wrong)

### Minimal Implementation (backend/app/main.py)

Add this to the SAME FastAPI `app` that uvicorn runs (`uvicorn app.main:app`):

from fastapi import HTTPException
from sqlalchemy import text
from app.db.session import SessionLocal

@app.get("/api/tickets/{client_guid}")
def api_ticket_lookup(client_guid: str):
db = SessionLocal()
try:
q = text("""
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
where t.client_guid = :guid
order by a.assigned_at desc nulls last
limit 1
""")
row = db.execute(q, {"guid": client_guid}).mappings().first()
if not row:
raise HTTPException(status_code=404, detail="Ticket GUID not found in DB")
return dict(row)
finally:
db.close()

Rebuild after changes:
docker compose up --build -d

---

## Frontend (10–15 min UI): Search Ticket by GUID

This is a single HTML page served by a static server.

### Run

1. Ensure backend is running on:
   http://localhost:8000

2. Start frontend static server:

From repo root:
cd frontend
python -m http.server 5173

Open:
http://localhost:5173

Paste a GUID, press Find → you get assignment + AI enrichment + raw JSON.

### Files

- `frontend/index.html` — UI layout
- `frontend/app.js` — fetches `GET {BackendURL}/api/tickets/{guid}`
- `frontend/main.css` — minimal styling

If frontend says it cannot reach backend:

- confirm `http://localhost:8000/docs` opens
- confirm the lookup endpoint exists in docs: `GET /api/tickets/{client_guid}`
- if your backend is containerized but frontend also in docker, use service name instead of localhost

---

## How Assignment Works (High Level)

1. AI enrichment:

- Detect language (fastText)
- LLM-based classification/summary/urgency/sentiment (via `llm_client.py` + prompts)

2. Routing constraints:

- VIP tickets: only eligible VIP managers
- “Смена данных” / data-change: only chief manager
- Language filtering: must match manager language skills

3. Final assignment:

- pick among eligible managers
- prefer lower load
- apply Round-Robin state for fairness
- store results in DB (`assignments` table)

---

## Common Issues

### 1) /api/tickets/{guid} returns 404 Not Found (endpoint)

You did not register the route in the FastAPI `app` used by uvicorn.
Fix: add endpoint to `backend/app/main.py` where `app = FastAPI()` is defined, rebuild.

### 2) Ticket GUID not found

Run pipeline:
python /app/scripts/seed_db.py
python /app/scripts/run_batch.py

### 3) LLM not reachable

If you use Ollama on host, ensure it is running and the backend container can reach it.
(If needed, expose host via `host.docker.internal` on Windows/Mac.)

---

## Outputs

- DB tables: tickets + ticket_ai + assignments + managers + business_units
- Final exported CSV:
  - `/app/data/results.csv` inside container
  - copy to host with `docker cp`

---

## Hackathon Deliverable Notes

- System is fully reproducible via Docker Compose
- Simple UI provided for demo:
  - enter GUID → instantly show assigned manager + office + AI enrichment
- Final results CSV can be generated with the one-command pipeline above

---
