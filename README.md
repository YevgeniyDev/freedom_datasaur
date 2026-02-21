Freedom Datasaur — FIRE Ticket Routing Service (Hackathon)
====================================================

Automatic overnight ticket processing pipeline:

CSV ingest → AI enrichment (LLM + fastText + heuristics + OCR for attachments) → deterministic routing + load balancing →
stored in PostgreSQL → export to CSV (and ready for UI visualization).

----------------------------------------------------
What it does
----------------------------------------------------

Input (3 CSVs)
- data/tickets.csv
  - client GUID, segment (Mass/VIP/Priority), description, attachment filename (e.g. order_error.png), address fields
- data/managers.csv
  - name, position (Спец/Ведущий спец/Глав спец), skills (VIP/ENG/KZ), office, current load
- data/business_units.csv
  - office name + office address

AI enrichment (stored in ticket_ai)
For each ticket we store:
- type_category (7 types):
  Жалоба / Смена данных / Консультация / Претензия /
  Неработоспособность приложения / Мошеннические действия / Спам
- sentiment: Позитивный / Нейтральный / Негативный
- urgency: 1..10
  - Business rule: VIP/Priority urgency is boosted to >= 8
- language: RU / ENG / KZ
  - Final language is decided by fastText + heuristics (may override LLM)
  - If the text looks like a different language (e.g., Uzbek-like latin text), we route as RU per spec but set needs_review=true
    and add a warning note in summary
- summary: 1–2 sentences with what the manager should do (includes language warning if unknown-like)
- recommended_actions: list of actions
- confidence: JSON debug info (LLM output, fastText top-k, OCR preview, rule overrides)

Attachment OCR (real image understanding)
- Tickets may include an attachment filename (e.g., order_error.png).
- The service resolves attachments relative to /app/data (mounted from ./data).
- pytesseract OCR extracts text from images and injects it into the LLM prompt and rule engine.
- OCR text is stored (preview) in ticket_ai.confidence["attachment_ocr"] for proof/debugging.

Rule guardrails (prevents common LLM mistakes)
Before trusting the LLM category, we apply deterministic overrides:
- Spam patterns (links, crypto/casino/etc) → type_category = "Спам"
- Verification/registration/doc-language issues (incl. Dia) → "Неработоспособность приложения"
- Payment failures ("не проходит оплата", "ошибка платежа") → "Неработоспособность приложения"
- Strong personal-data change intent (phone/email/IIN/passport/etc) → "Смена данных"
- Commission/fees questions ("комиссия/удержание/списание/обслуживание") → "Консультация"
These overrides are stored in confidence["rule_override"].

Deterministic routing (stored in assignments)
Hard business rules:
1) Office selection
   - Kazakhstan + city → office match (exact/contains + fuzzy)
   - Unknown / abroad / missing city → stable 50/50 Astana/Almaty (hash by GUID)
   - (Geocoding + nearest-office is a planned improvement)
2) Eligibility filters (hard constraints)
   - VIP/Priority → only managers with VIP skill
   - "Смена данных" → only "Глав спец" (robust normalization by substring "глав")
   - Language ENG/KZ → manager must have corresponding skill
3) Load balancing
   - Pick 2 eligible managers with lowest effective load
   - Alternate via Round Robin with Postgres row-lock table rr_state
4) Explainability
   - assignments.decision_trace records office reason, constraints, eligible set, loads, RR pick, notes

Spam handling
- Tickets classified as "Спам" are enriched and stored in ticket_ai, but are NOT assigned to a manager.

----------------------------------------------------
Repo structure (main parts)
----------------------------------------------------

```bash
backend/app/main.py                FastAPI entrypoint (health endpoint)
backend/app/db/models.py           SQLAlchemy models (tickets, ticket_ai, managers, business_units, assignments, rr_state)
backend/alembic/                   Migrations
backend/app/ai/                    AI enrichment
  - llm_client.py                  Ollama client (JSON output)
  - prompts.py                     Strict system prompt + user prompt (includes OCR text)
  - enrich.py                      Rule overrides + OCR + fastText/heuristics + writes ticket_ai
  - lang_detect.py                 fastText language detection helper
scripts/seed_db.py                 CSV → DB seeding (cleans tables + inserts)
scripts/run_batch.py               Batch: enrich + choose office + filter + allocate RR + write assignments
data/                              CSVs and attachments (e.g. order_error.png)
backend/Dockerfile                 Backend container (installs tesseract + build tools for fasttext)
```

----------------------------------------------------
Requirements / Tools
----------------------------------------------------
- Docker Desktop (Windows)
- Ollama running locally on host (for LLM)
- fastText model lid.176.bin (download once; not committed)
- pytesseract + tesseract-ocr inside backend container (installed via Dockerfile)

----------------------------------------------------
Setup (Dockerized backend + Postgres)
----------------------------------------------------

1) Start services
From repo root:
  `docker compose up --build`

Expected logs:
  `fire_backend | Uvicorn running on http://0.0.0.0:8000`

Health check (browser):
  `http://localhost:8000/health`

2) .env (optional, if you run scripts locally)
If you run inside container, compose env is enough. For local runs, create .env:
  ```bash
  DATABASE_URL=postgresql+psycopg2://fire:fire@127.0.0.1:55432/fire
  OLLAMA_BASE_URL=http://localhost:11434
  OLLAMA_MODEL=qwen2.5:3b-instruct
  FASTTEXT_LID_PATH=backend/app/ai/models/lid.176.bin
  ```

3) fastText language model (lid.176.bin)
Download once (PowerShell, repo root):
  `mkdir backend\app\ai\models -Force`
  `Invoke-WebRequest -Uri "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin" -OutFile "backend\app\ai\models\lid.176.bin"`

Do NOT commit:
  `backend/app/ai/models/*.bin`

----------------------------------------------------
Run migrations + seed + batch (inside container)
----------------------------------------------------

Open a shell:
  `docker exec -it fire_backend bash`

Then:
  `cd /app/backend`
  `alembic upgrade head`

Seed (loads CSVs, clears derived tables):
  `python /app/scripts/seed_db.py`

Run batch (enrich + route + assign; spam is not assigned):
  `python /app/scripts/run_batch.py`

Expected counts (example):
  ```bash
  tickets 31
  ticket_ai 31
  assignments 28
  spam not assigned 3
  ```

----------------------------------------------------
Export results to CSV (Excel-friendly)
----------------------------------------------------

Inside container (writes UTF-8 with BOM via utf-8-sig):
  ```bash
  python - << 'PY'
import os, pandas as pd
from sqlalchemy import create_engine, text
e = create_engine(os.environ["DATABASE_URL"])
q = """
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
  left(coalesce(ai.confidence->>'attachment_ocr',''), 200) as ocr_preview,
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
"""
df = pd.read_sql_query(text(q), e)
df.to_csv('/app/data/results.csv', index=False, encoding='utf-8-sig')
print('Wrote /app/data/results.csv rows:', len(df))
PY
```

Copy to Windows (run in PowerShell on host, NOT inside container):

  `docker cp fire_backend:/app/data/results.csv .\results.csv`

Transform to Excel format (to avoid artifacts):

  ` python -c "p=open('results.csv','rb').read(); open('results_excel.csv','wb').write(b'\xef\xbb\xbf'+p)"`

----------------------------------------------------
Quick checks (SQL)
----------------------------------------------------

Hard-rule violations should be 0:
- VIP skill:
  ```bash
  select count(*) from assignments a
  join tickets t on t.id=a.ticket_id
  join managers m on m.id=a.manager_id
  where t.segment in ('VIP','Priority') and not ('VIP'=any(m.skills));
  ```
- Data-change → only chief:
```bash
  select count(*) from assignments a
  join ticket_ai ai on ai.ticket_id=a.ticket_id
  join managers m on m.id=a.manager_id
  where ai.type_category='Смена данных' and lower(m.position) not like '%глав%';
```
- Language skills:
  ```bash
  select count(*) from assignments a
  join ticket_ai ai on ai.ticket_id=a.ticket_id
  join managers m on m.id=a.manager_id
  where ai.language in ('ENG','KZ') and not (ai.language=any(m.skills));
  ```
OCR proof:
- show rows where OCR was stored:
```bash
  select t.attachment_path, left(ai.confidence->>'attachment_ocr', 200)
  from tickets t join ticket_ai ai on ai.ticket_id=t.id
  where ai.confidence ? 'attachment_ocr';
```

----------------------------------------------------
Notes / Next improvements
----------------------------------------------------
- Replace city-match office selection with geocoding + nearest-office distance (store lat/lon for offices and tickets).
- Add a simple UI (React/Vite): list tickets, show summary/actions, show decision_trace, filters by office/category/lang.
- Star Task: NL query → safe intent mapping → predefined SQL → charts (no free-form SQL for safety).
