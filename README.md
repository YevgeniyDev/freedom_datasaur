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

`docker compose up --build -d`

2. Run:

`docker exec -it fire_backend bash`
    
3. Run migrations, seed DB, process tickets, export results:
```bash
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
```

4. To view results in CSV format - open new Terminal window, go to the root repo and copy CSV to host:

Windows PowerShell:

`docker cp fire_backend:/app/data/results.csv .\results.csv`

macOS/Linux:

`docker cp fire_backend:/app/data/results.csv ./results.csv`

5. Transform it into viewable Excel formatting:

`python -c "p=open('results.csv','rb').read(); open('results_excel.csv','wb').write(b'\xef\xbb\xbf'+p)"`

Now you can check it in the ROOT Repo - 'results_excel.csv'

---

## Frontend: Search Ticket by GUID

This is a single HTML page served by a static server.

### Run

1. Ensure backend is running on:
   
   `http://localhost:8000`

3. Start frontend static server:

From repo root:

`cd frontend`

`python -m http.server 5173`

Open:

`http://localhost:5173`

Paste a GUID, press Find → you get assignment + AI enrichment + raw JSON.

### Files

- `frontend/index.html` — UI layout
- `frontend/app.js` — fetches `GET {BackendURL}/api/tickets/{guid}`
- `frontend/main.css` — minimal styling

If frontend says it cannot reach backend:

- confirm `http://localhost:8000/docs` opens
- confirm the lookup endpoint exists in docs: `GET /api/tickets/{client_guid}`
  
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

## Outputs

- DB tables: tickets + ticket_ai + assignments + managers + business_units
- Final exported CSV:
  - `/app/data/results.csv` inside container
  - copy to host with `docker cp`

---

## Common Issues

### 1) Ticket GUID not found

In the Docker container un pipeline:
```bash
python /app/scripts/seed_db.py
python /app/scripts/run_batch.py
```

### 2) LLM not reachable

If you use Ollama on host, ensure it is running and the backend container can reach it.
(If needed, expose host via `host.docker.internal` on Windows/Mac.)

---

## Debug / Verification

Check backend docs:

`http://localhost:8000/docs`

Check logs:

`docker logs fire_backend --tail 100`

---


## Hackathon Deliverable Notes

- System is fully reproducible via Docker Compose
- Simple UI provided for demo:
  - enter GUID → instantly show assigned manager + office + AI enrichment
- Final results CSV can be generated with the one-command pipeline above

---
