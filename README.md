# FIRE: Ticket Intelligence + Routing (Freedom Broker Hackathon)

## Overview

This project implements an offline-hours service that:

1. Ingests 3 CSV tables (tickets, managers, business units/offices).
2. Enriches each ticket with AI analytics (category, sentiment, urgency, language, summary + recommended actions).
3. Assigns each ticket to the best manager using strict business rules:
   - nearest office (phase-1: city match, phase-2: geocoding)
   - hard-skill filters (VIP, language, “Смена данных”)
   - balancing: top-2 lowest load + Round Robin
4. Stores everything in PostgreSQL with traceability:
   Ticket -> TicketAI -> Assignment (manager + office + decision_trace).
5. Supports “needs_review” flags (e.g., uncertain language).

## Repository Layout

backend/
app/
ai/
llm_client.py # Ollama client (Qwen local)
prompts.py # Strict JSON prompt for enrichment
schema.py # Pydantic schema for AI output
enrich.py # Enrich Ticket -> TicketAI (includes language override)
lang_detect.py # fastText language detection
models/ # lid.176.bin is placed here (NOT committed)
db/
models.py # SQLAlchemy models
session.py # DB session helper (db_session)
routing/
rules.py # Hard-skill filters (VIP/Chief/Language)
allocator.py # top2 lowest-load + RR with row lock (rr_state)
trace.py # decision_trace builder
frontend/
(React + Vite UI - to be added/extended)
scripts/
seed_db.py # Loads CSV into Postgres
run_batch.py # Runs enrichment + routing + assignments
data/
tickets.csv
managers.csv
business_units.csv
docker-compose.yml
.env (local only)

## Requirements

- Windows laptop for development:
  - Python 3.11+
  - Docker Desktop
  - Node.js 18+ (for React UI)
  - Ollama (local LLM runtime)
- PostgreSQL runs in Docker.
- Open-source LLM only:
  - Qwen2.5 (CPU via Ollama now; GPU via vLLM later)
- Language detection:
  - fastText lid.176.bin (downloaded locally)
  - NOTE: fastText-wheel requires NumPy < 2.0

## Setup (Windows)

1. Create Python venv and install deps
   - From repo root:
     py -3.11 -m venv .venv
     .venv\Scripts\Activate.ps1
     pip install -r backend\requirements.txt

2. Start PostgreSQL in Docker
   - From repo root:
     docker compose up -d

   IMPORTANT: If you have a local Windows Postgres service, use a non-standard port mapping
   in docker-compose.yml (example):
   ports: - "55432:5432"
   Then in .env use port 55432.

3. Configure environment
   Create repo-root .env:
   DATABASE_URL=postgresql+psycopg2://fire:fire@127.0.0.1:55432/fire
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=qwen2.5:3b-instruct
   FASTTEXT_LID_PATH=backend/app/ai/models/lid.176.bin

4. Install Ollama + pull Qwen model (CPU)
   - Install Ollama (Windows installer)
   - Pull model:
     ollama pull qwen2.5:3b-instruct

5. Download fastText language model (lid.176.bin)
   - Create folder:
     mkdir backend\app\ai\models -Force
   - Download:
     Invoke-WebRequest -Uri "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin" `
     -OutFile "backend\app\ai\models\lid.176.bin"

   Add to .gitignore:
   backend/app/ai/models/\*.bin

6. Fix NumPy compatibility (if fastText crashes)
   - If you see NumPy 2.0 errors with fastText:
     pip install "numpy<2" --force-reinstall

## Database Migrations

We use Alembic to create tables:

- tickets
- ticket_ai
- business_units
- managers
- assignments
- rr_state

Run from backend/:
cd backend
alembic revision --autogenerate -m "init schema"
alembic upgrade head

## Seeding Data (CSV -> Postgres)

CSV files must be placed in data/:

- data/tickets.csv
- data/managers.csv
- data/business_units.csv

Seed:
python scripts/seed_db.py

NOTE: tickets.csv headers include:

- "Населённый пункт" (ё) and "Описание " (extra space).
  seed_db.py strips headers and maps correctly.

If you previously seeded wrong data, reset:
docker exec -it fire_db psql -U fire -d fire -c "TRUNCATE assignments, rr_state, ticket_ai, tickets, managers, business_units;"

## Batch Processing (AI + Routing)

Run full pipeline:
docker exec -it fire_db psql -U fire -d fire -c "TRUNCATE assignments, rr_state, ticket_ai;"
python scripts/run_batch.py

What it does for each ticket:

1. AI enrichment (Qwen via Ollama):
   - type_category: {Жалоба, Смена данных, Консультация, Претензия,
     Неработоспособность приложения, Мошеннические действия, Спам}
   - sentiment: {Позитивный, Нейтральный, Негативный}
   - urgency: 1..10
   - summary: 1–2 sentences + manager next steps
2. Language detection (fastText) overrides LLM when confident:
   - supported output: RU / ENG / KZ
   - unknown/mixed cases are flagged needs_review=true (spec default RU)
3. Office selection:
   - If country missing/not Kazakhstan => deterministic 50/50 Astana/Almaty
   - Else city match / fuzzy match against office names
   - If city not an office city => treat as unknown => 50/50 Astana/Almaty
4. Hard-skill filters:
   - VIP/Priority => manager must have VIP skill
   - “Смена данных” => manager must be “Глав спец” (robust match: contains “глав”)
   - language ENG/KZ => manager must have corresponding skill
5. Balancing:
   - Compute effective load = current_load + assignments in batch
   - Pick top 2 lowest load managers
   - Round Robin between them using rr_state row lock (pair-specific bucket)
6. Save:
   - ticket_ai row (enrichment)
   - assignments row (manager + office) with decision_trace JSON

## Key Business Rules Compliance Checks

1. VIP/Priority only to VIP-skilled managers:
   docker exec -it fire_db psql -U fire -d fire -c "
   select count(\*) as vip_rule_violations
   from assignments a
   join tickets t on t.id=a.ticket_id
   join managers m on m.id=a.manager_id
   where t.segment in ('VIP','Priority')
   and NOT ('VIP' = ANY(m.skills));"

2. “Смена данных” only to chief specialists:
   docker exec -it fire_db psql -U fire -d fire -c "
   select count(\*) as datachange_rule_violations
   from assignments a
   join ticket_ai ai on ai.ticket_id=a.ticket_id
   join managers m on m.id=a.manager_id
   where ai.type_category='Смена данных'
   and lower(m.position) not like '%глав%';"

3. ENG/KZ only to managers with language skill:
   docker exec -it fire_db psql -U fire -d fire -c "
   select count(\*) as lang_rule_violations
   from assignments a
   join ticket_ai ai on ai.ticket_id=a.ticket_id
   join managers m on m.id=a.manager_id
   where ai.language in ('ENG','KZ')
   and NOT (ai.language = ANY(m.skills));"

## Inspecting Results (SQL)

1. Category distribution + urgency stats:
   docker exec -it fire_db psql -U fire -d fire -c "
   select type_category, count(\*) n,
   round(avg(urgency)::numeric,2) avg_u,
   min(urgency) min_u, max(urgency) max_u
   from ticket_ai group by 1 order by n desc;"

2. Full joined view (ticket + AI + assignment):
   docker exec -it fire_db psql -U fire -d fire -c "
   select t.id, t.segment, t.country, t.city,
   ai.language, ai.type_category, ai.sentiment, ai.urgency,
   bu.office_name office, m.full_name manager,
   left(coalesce(ai.summary,''),120) summary_preview
   from tickets t
   join ticket_ai ai on ai.ticket_id=t.id
   left join assignments a on a.ticket_id=t.id
   left join managers m on m.id=a.manager_id
   left join business_units bu on bu.id=a.business_unit_id
   order by ai.urgency desc;"

3. needs_review cases:
   docker exec -it fire_db psql -U fire -d fire -c "
   select t.id, ai.language, ai.type_category, ai.urgency,
   ai.confidence->'fasttext_top5' ft_top5,
   left(t.description,200) snippet
   from tickets t join ticket_ai ai on ai.ticket_id=t.id
   where ai.needs_review=true;"

## Frontend (React + Vite) - Next Step

Goal UI:

- Ticket list table: segment, type, language, urgency, office, manager
- Ticket details panel: original description + AI summary + actions
- Explainability panel: decision_trace JSON (office selection + filters + top2 + RR)

Recommended API endpoints (to implement in FastAPI):

- GET /tickets
- GET /tickets/{id} (includes AI + assignment + trace)
- POST /process/run_batch (optional)

## Star Task (later)

Add an in-UI assistant that converts natural language requests into a safe, whitelisted chart intent:
Example: “Покажи распределение типов обращений по городам”
LLM -> {"metric":"tickets_by_city","chart":"bar",...}
Backend executes only predefined queries and returns data to UI for charts.

## GPU Upgrade Plan (Linux + RTX 3090)

- Replace Ollama CPU with vLLM on GPU:
  - Qwen2.5-7B-Instruct (FP16 or AWQ)
- Keep same API contract in llm_client.py (base_url changes)
- Batch time per ticket should drop significantly (<10s target is easy).

## Troubleshooting

1. Password authentication failed:

- You likely connected to Windows postgres.exe instead of Docker.
- Use a unique port mapping (e.g., 55432) and update DATABASE_URL accordingly.

2. fastText crashes with NumPy 2.x:

- pip install "numpy<2" --force-reinstall

3. City missing in DB:

- Ensure seed_db.py maps tickets.csv column "Населённый пункт" (ё)
- If previously seeded wrong, TRUNCATE tables and reseed.

## License/Notes

- This repo uses open-source components only for LLM (Qwen) and language detection (fastText).
- Model binaries (lid.176.bin) are not committed; download locally.
