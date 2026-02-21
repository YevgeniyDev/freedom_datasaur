# Freedom Datasaur — FIRE Ticket Routing Service (Hackathon)

Service for automatic overnight ticket processing:
CSV ingest → AI enrichment (LLM + language detection + optional geocoding) → deterministic routing + load balancing → stored in PostgreSQL → ready for UI visualization.

---

## What it does

### Input (3 CSVs)

- `tickets.csv`: client GUID, segment (Mass/VIP/Priority), free-text description, attachment pointer, address fields.
- `managers.csv`: name, position (Спец/Ведущий спец/Глав спец), skills (VIP/ENG/KZ), office, current load.
- `business_units.csv`: office name + office address.

### AI enrichment (stored in `ticket_ai`)

For each ticket:

- `type_category` (7 types): Жалоба / Смена данных / Консультация / Претензия / Неработоспособность приложения / Мошеннические действия / Спам
- `sentiment`: Позитивный / Нейтральный / Негативный
- `urgency`: 1..10
- `language`: RU/ENG/KZ
  - Detected via fastText + heuristics and may override LLM output
  - Unknown-like texts are flagged with `needs_review=true`
- `summary`: 1–2 sentences + recommended actions
- `recommended_actions`: list of actions
- `confidence`: JSON with LLM/fastText debug info

### Deterministic routing (stored in `assignments`)

Hard business rules:

1. Nearest office (currently: city→office match + 50/50 Astana/Almaty fallback; geocoding later).
   - If address unknown/abroad → 50/50 Astana/Almaty (stable hash by GUID)
2. Hard skills filters:
   - VIP/Priority → only managers with `VIP`
   - “Смена данных” → only “Глав спец” (robust position normalization)
   - Language ENG/KZ → manager must have corresponding skill
3. Load balancing:
   - Choose 2 eligible managers with lowest effective load
   - Distribute via Round Robin with Postgres row lock (`rr_state`)
4. Full explainability:
   - `assignments.decision_trace` stores “why office” + filters + candidate loads + RR decision

---

## Repo structure (main parts)

- `backend/app/db/models.py` — SQLAlchemy models
- `backend/alembic/` — migrations
- `scripts/seed_db.py` — load CSVs → DB (idempotent via GUID upsert)
- `scripts/run_batch.py` — enrich + route + assign
- `backend/app/ai/` — LLM + language detection
  - `llm_client.py` (Ollama)
  - `enrich.py` (writes `ticket_ai`)
  - `lang_detect.py` (fastText)
  - `models/lid.176.bin` (not committed)

---

## Requirements

### Windows laptop (CPU)

- Python 3.11+
- Docker Desktop
- Node.js 18+ (for React/Vite UI)
- Ollama (local LLM runner)

### Linux + GPU (optional)

- RTX 3090 machine can run a bigger Qwen model (vLLM), but CPU Ollama works for MVP.

---

## Setup (Windows)

### 1) Start PostgreSQL (Docker)

We use a high host port to avoid collisions with local Windows Postgres.

In `docker-compose.yml`:

- host port: `55432`
- container port: `5432`

Start:

- `docker compose up -d`

### 2) Python venv

From repo root:

- `py -3.11 -m venv .venv`
- `.venv\Scripts\Activate.ps1`
- `pip install -r backend\requirements.txt`

### 3) .env

Create repo-root `.env`:

- `DATABASE_URL=postgresql+psycopg2://fire:fire@127.0.0.1:55432/fire`
- `OLLAMA_BASE_URL=http://localhost:11434`
- `OLLAMA_MODEL=qwen2.5:3b-instruct`
- `FASTTEXT_LID_PATH=backend/app/ai/models/lid.176.bin`

---

## Database migrations (Alembic)

From `backend/`:

- `alembic upgrade head`

(If schema changes:)

- `alembic revision --autogenerate -m "..." && alembic upgrade head`

---

## Local LLM (Ollama)

Install Ollama, then:

- `ollama pull qwen2.5:3b-instruct`

The batch script calls Ollama via HTTP:

- `http://localhost:11434/api/chat`

---

## fastText language model (lid.176.bin)

Download once (PowerShell, repo root):

- `mkdir backend\app\ai\models -Force`
- `Invoke-WebRequest -Uri "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin" -OutFile "backend\app\ai\models\lid.176.bin"`

NOTE: fastText wheel may require NumPy < 2:

- `pip install "numpy<2" --force-reinstall`

Do NOT commit `.bin`:
Add to `.gitignore`:

- `backend/app/ai/models/*.bin`

---

## Load CSV data into DB

Put CSV files in:

- `data/tickets.csv`
- `data/managers.csv`
- `data/business_units.csv`

Seed:

- `python scripts/seed_db.py`

Seed behavior:

- Clears derived tables (`assignments`, `rr_state`, `ticket_ai`)
- Upserts tickets by `client_guid` (CSV GUID) so GUID is preserved and stable

Verify counts (optional):

- `docker exec -it fire_db psql -U fire -d fire -c "select count(*) from tickets;"`
- `docker exec -it fire_db psql -U fire -d fire -c "select count(*) from managers;"`
- `docker exec -it fire_db psql -U fire -d fire -c "select count(*) from business_units;"`

---

## Run the pipeline (AI + routing + assignments)

1. Clear derived tables:

- `docker exec -it fire_db psql -U fire -d fire -c "TRUNCATE assignments, rr_state, ticket_ai;"`

2. Run batch:

- `python scripts/run_batch.py`

Outputs:

- `ticket_ai` filled for every ticket
- `assignments` contains final manager assignment
- `decision_trace` explains each decision

---

## Quick checks (SQL)

Language distribution:

- `select language, count(*) from ticket_ai group by 1 order by 2 desc;`

Category distribution + urgency stats:

- `select type_category, count(*), round(avg(urgency)::numeric,2) avg_u from ticket_ai group by 1 order by 2 desc;`

Hard-rule violations (should be 0):

- VIP rule:
  `select count(*) from assignments a join tickets t on t.id=a.ticket_id join managers m on m.id=a.manager_id where t.segment in ('VIP','Priority') and not ('VIP'=any(m.skills));`
- Data-change rule:
  `select count(*) from assignments a join ticket_ai ai on ai.ticket_id=a.ticket_id join managers m on m.id=a.manager_id where ai.type_category='Смена данных' and lower(m.position) not like '%глав%';`
- Language rule:
  `select count(*) from assignments a join ticket_ai ai on ai.ticket_id=a.ticket_id join managers m on m.id=a.manager_id where ai.language in ('ENG','KZ') and not (ai.language=any(m.skills));`

---

## Export results to CSV (for quick inspection)

Export joined view into container, then copy to Windows:

1. Export inside container:
   docker exec -it fire_db psql -U fire -d fire -c "\copy (
   select
   t.client_guid as ticket_guid,
   t.segment,
   t.country,
   t.city,
   ai.language,
   ai.type_category,
   ai.sentiment,
   ai.urgency,
   bu.office_name as office,
   m.full_name as manager,
   ai.needs_review,
   ai.summary
   from tickets t
   join ticket_ai ai on ai.ticket_id=t.id
   left join assignments a on a.ticket_id=t.id
   left join managers m on m.id=a.manager_id
   left join business_units bu on bu.id=a.business_unit_id
   order by ai.urgency desc
   ) TO '/tmp/results.csv' WITH CSV HEADER;"

2. Copy to local:

- `docker cp fire_db:/tmp/results.csv .\results.csv`

---

## Notes / Next improvements

- Replace “city match” office selection with full geocoding + nearest-office distance (store lat/lon for offices and tickets).
- Add OCR for attachments and pass extracted text into enrichment.
- Build React/Vite UI:
  - table view: ticket GUID + AI fields + manager
  - ticket detail: summary/actions + decision_trace “why assigned”
  - Star Task: NL query → safe intent → charts
