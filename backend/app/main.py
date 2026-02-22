from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from typing import Optional, Any, Dict, List
from datetime import datetime

from app.db.session import SessionLocal

app = FastAPI()

# allow local frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hackathon speed; tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------
# Existing: GUID lookup
# -----------------------
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

          bu.id as office_id,
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


# -----------------------
# NEW: offices dropdown
# -----------------------
@app.get("/api/offices")
def list_offices():
    db = SessionLocal()
    try:
        q = text("""
        select id, office_name, address
        from business_units
        order by office_name
        """)
        rows = db.execute(q).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


# -----------------------
# NEW: list tickets with filters + pagination
# -----------------------
@app.get("/api/tickets")
def list_tickets(
    office_id: Optional[str] = None,
    assigned: Optional[str] = Query(default=None, description="all|assigned|unassigned"),
    segment: Optional[str] = None,
    category: Optional[str] = None,
    language: Optional[str] = Query(default=None, description="RU|ENG|KZ"),
    needs_review: Optional[bool] = None,
    min_urgency: Optional[int] = Query(default=None, ge=1, le=10),
    max_urgency: Optional[int] = Query(default=None, ge=1, le=10),
    q: Optional[str] = Query(default=None, description="search in guid/summary/description"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    db = SessionLocal()
    try:
        where: List[str] = []
        params: Dict[str, Any] = {"limit": limit, "offset": offset}

        # assigned filter
        if assigned == "assigned":
            where.append("a.id is not null")
        elif assigned == "unassigned":
            where.append("a.id is null")

        # office filter (only makes sense for assigned)
        if office_id:
            where.append("a.business_unit_id = :office_id")
            params["office_id"] = office_id

        if segment:
            where.append("lower(coalesce(t.segment,'')) = lower(:segment)")
            params["segment"] = segment

        if category:
            where.append("lower(coalesce(ai.type_category,'')) = lower(:category)")
            params["category"] = category

        if language:
            where.append("upper(coalesce(ai.language,'')) = upper(:language)")
            params["language"] = language

        if needs_review is not None:
            where.append("coalesce(ai.needs_review,false) = :needs_review")
            params["needs_review"] = bool(needs_review)

        if min_urgency is not None:
            where.append("coalesce(ai.urgency,0) >= :min_urgency")
            params["min_urgency"] = int(min_urgency)

        if max_urgency is not None:
            where.append("coalesce(ai.urgency,0) <= :max_urgency")
            params["max_urgency"] = int(max_urgency)

        if q:
            where.append("""
            (
              t.client_guid ilike :q
              or coalesce(ai.summary,'') ilike :q
              or coalesce(t.description,'') ilike :q
            )
            """)
            params["q"] = f"%{q}%"

        where_sql = ("where " + " and ".join(where)) if where else ""

        # total
        total_q = text(f"""
        select count(*) as total
        from tickets t
        left join ticket_ai ai on ai.ticket_id = t.id
        left join assignments a on a.ticket_id = t.id
        {where_sql}
        """)
        total = db.execute(total_q, params).scalar_one()

        # items
        items_q = text(f"""
        select
          t.client_guid,
          t.segment,
          coalesce(ai.type_category, '') as type_category,
          coalesce(ai.urgency, null) as urgency,
          coalesce(ai.language, '') as final_language,
          coalesce(ai.needs_review, false) as needs_review,
          coalesce(ai.summary, '') as summary,
          bu.id as office_id,
          bu.office_name as assigned_office,
          m.full_name as assigned_manager,
          a.assigned_at
        from tickets t
        left join ticket_ai ai on ai.ticket_id = t.id
        left join assignments a on a.ticket_id = t.id
        left join managers m on m.id = a.manager_id
        left join business_units bu on bu.id = a.business_unit_id
        {where_sql}
        order by
          (a.assigned_at is null) desc,   -- assigned first
          a.assigned_at desc nulls last,
          t.client_guid
        limit :limit offset :offset
        """)
        rows = db.execute(items_q, params).mappings().all()
        return {"items": [dict(r) for r in rows], "total": int(total)}
    finally:
        db.close()


# -----------------------
# NEW: dashboard stats for charts
# -----------------------
@app.get("/api/stats")
def stats(
    office_id: Optional[str] = None,
    segment: Optional[str] = None,
    language: Optional[str] = None,
):
    """
    Returns aggregates for dashboard charts.
    Filters are optional and apply to ALL aggregates.
    """
    db = SessionLocal()
    try:
        where: List[str] = []
        params: Dict[str, Any] = {}

        if office_id:
            where.append("a.business_unit_id = :office_id")
            params["office_id"] = office_id

        if segment:
            where.append("lower(coalesce(t.segment,'')) = lower(:segment)")
            params["segment"] = segment

        if language:
            where.append("upper(coalesce(ai.language,'')) = upper(:language)")
            params["language"] = language

        where_sql = ("where " + " and ".join(where)) if where else ""

        # KPIs
        kpi_q = text(f"""
        select
          count(*) as total,
          sum(case when a.id is not null then 1 else 0 end) as assigned,
          sum(case when a.id is null then 1 else 0 end) as unassigned,
          sum(case when coalesce(ai.needs_review,false) then 1 else 0 end) as needs_review,
          sum(case when lower(coalesce(ai.type_category,'')) = 'спам' then 1 else 0 end) as spam,
          avg(nullif(ai.urgency,0)) as avg_urgency
        from tickets t
        left join ticket_ai ai on ai.ticket_id = t.id
        left join assignments a on a.ticket_id = t.id
        {where_sql}
        """)
        kpi = db.execute(kpi_q, params).mappings().first() or {}

        # by category
        by_cat_q = text(f"""
        select coalesce(ai.type_category,'(none)') as key, count(*) as value
        from tickets t
        left join ticket_ai ai on ai.ticket_id = t.id
        left join assignments a on a.ticket_id = t.id
        {where_sql}
        group by coalesce(ai.type_category,'(none)')
        order by value desc
        """)
        by_category = [dict(r) for r in db.execute(by_cat_q, params).mappings().all()]

        # by office (assigned only)
        by_office_q = text(f"""
        select coalesce(bu.office_name,'(unassigned)') as key, count(*) as value
        from tickets t
        left join ticket_ai ai on ai.ticket_id = t.id
        left join assignments a on a.ticket_id = t.id
        left join business_units bu on bu.id = a.business_unit_id
        {where_sql}
        group by coalesce(bu.office_name,'(unassigned)')
        order by value desc
        """)
        by_office = [dict(r) for r in db.execute(by_office_q, params).mappings().all()]

        # urgency histogram 1..10
        urgency_q = text(f"""
        select coalesce(ai.urgency,0) as key, count(*) as value
        from tickets t
        left join ticket_ai ai on ai.ticket_id = t.id
        left join assignments a on a.ticket_id = t.id
        {where_sql}
        group by coalesce(ai.urgency,0)
        order by key asc
        """)
        urg_rows = [dict(r) for r in db.execute(urgency_q, params).mappings().all()]
        # normalize into 1..10 bins
        urg_map = {int(r["key"]): int(r["value"]) for r in urg_rows if r["key"] is not None}
        urgency_hist = [{"key": i, "value": int(urg_map.get(i, 0))} for i in range(1, 11)]

        # language distribution
        lang_q = text(f"""
        select upper(coalesce(ai.language,'(none)')) as key, count(*) as value
        from tickets t
        left join ticket_ai ai on ai.ticket_id = t.id
        left join assignments a on a.ticket_id = t.id
        {where_sql}
        group by upper(coalesce(ai.language,'(none)'))
        order by value desc
        """)
        by_language = [dict(r) for r in db.execute(lang_q, params).mappings().all()]

        out = {
            "kpi": {
                "total": int(kpi.get("total") or 0),
                "assigned": int(kpi.get("assigned") or 0),
                "unassigned": int(kpi.get("unassigned") or 0),
                "needs_review": int(kpi.get("needs_review") or 0),
                "spam": int(kpi.get("spam") or 0),
                "avg_urgency": float(kpi.get("avg_urgency") or 0.0),
            },
            "by_category": by_category,
            "by_office": by_office,
            "urgency_hist": urgency_hist,
            "by_language": by_language,
        }
        return out
    finally:
        db.close()