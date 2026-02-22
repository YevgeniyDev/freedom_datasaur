from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
import os

from app.db.session import SessionLocal  # adjust if your path differs

app = FastAPI()

# allow local frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # for hackathon speed; tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/tickets/{client_guid}")
def get_ticket_by_guid(client_guid: str):
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
            raise HTTPException(status_code=404, detail="Ticket not found")

        return dict(row)
    finally:
        db.close()
        