# scripts/run_batch.py
from __future__ import annotations

import hashlib
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

# --- Path setup so "app.*" imports work when running from repo root ---
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(REPO_ROOT / "backend"))

from app.ai.enrich import enrich_ticket  # noqa: E402
from app.ai.llm_client import OllamaClient  # noqa: E402
from app.db.models import Assignment, BusinessUnit, Manager, Ticket  # noqa: E402
from app.routing.allocator import allocate_round_robin  # noqa: E402
from app.routing.rules import RoutingNeeds, compute_needs, filter_managers  # noqa: E402
from app.routing.trace import build_decision_trace  # noqa: E402


# -----------------------
# Office selection helpers
# -----------------------
KZ_ALIASES = {"казахстан", "kazakhstan", "kz", "kazaqstan"}

CITY_SYNONYMS = {
    "aktau": "актау",
    "kosshy / astana": "астана",
    "косшы / астана": "астана",
    "косшы": "астана",
}


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower().replace("ё", "е")


def _is_kazakhstan(country: Optional[str]) -> bool:
    c = _norm(country)
    if not c:
        return False
    return c in KZ_ALIASES or "казахстан" in c or "kazakh" in c


def _stable_coin_flip(key: str) -> int:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(h[-1], 16) % 2  # 0/1


def _find_office(offices: List[BusinessUnit], *needles: str) -> Optional[BusinessUnit]:
    for needle in needles:
        n = _norm(needle)
        for o in offices:
            if n and n in _norm(o.office_name):
                return o
    return None


def _choose_astana_almaty(ticket: Ticket, offices: List[BusinessUnit], reason: str) -> Tuple[BusinessUnit, str]:
    astana = _find_office(offices, "астан", "astana")
    almaty = _find_office(offices, "алмат", "almaty")

    if astana and almaty:
        chosen = astana if _stable_coin_flip(ticket.client_guid or str(ticket.id)) == 0 else almaty
        return chosen, f"{reason} -> 50/50 Astana/Almaty"

    # fallback if names not found
    a = offices[0]
    b = offices[1] if len(offices) > 1 else offices[0]
    chosen = a if _stable_coin_flip(ticket.client_guid or str(ticket.id)) == 0 else b
    return chosen, f"{reason} -> 50/50 first-two offices"


def choose_business_unit(ticket: Ticket, offices: List[BusinessUnit]) -> Tuple[BusinessUnit, str]:
    """
    Real rule (phase 1, no geocode yet):
    - country missing / not Kazakhstan -> 50/50 Astana/Almaty
    - else try city->office match (exact/contains), then fuzzy on office_name
    - if still not an office city -> treat like unknown address -> 50/50 Astana/Almaty
    """
    if not offices:
        raise RuntimeError("No offices loaded")

    if not _is_kazakhstan(ticket.country):
        return _choose_astana_almaty(ticket, offices, "Unknown/abroad country")

    raw_city = (ticket.city or "").strip()
    city = _norm(raw_city)
    if not city:
        return _choose_astana_almaty(ticket, offices, "KZ but city missing (unknown address)")

    city = CITY_SYNONYMS.get(city, city)
    if "астана" in city:
        city = "астана"
    if "алматы" in city:
        city = "алматы"

    # exact/contains on office_name
    for o in offices:
        on = _norm(o.office_name)
        if city == on or city in on:
            return o, f"KZ city exact/contains match: '{raw_city}' -> '{o.office_name}'"

    # fuzzy match on office_name (normalized)
    office_norm_map = {_norm(o.office_name): o for o in offices}
    match = process.extractOne(city, list(office_norm_map.keys()), scorer=fuzz.WRatio)
    if match and match[1] >= 90:
        best = office_norm_map[match[0]]
        return best, f"KZ city fuzzy match: '{raw_city}' -> '{best.office_name}' (score={match[1]})"

    return _choose_astana_almaty(ticket, offices, f"KZ city '{raw_city}' not an office city (no geocode yet)")


# -----------------------
# Eligibility fallback
# -----------------------
def find_eligible_any_office(
    offices: List[BusinessUnit],
    managers: List[Manager],
    needs: RoutingNeeds,
    prefer_offices: List[BusinessUnit],
) -> Tuple[Optional[BusinessUnit], List[Manager], str]:
    """
    Deterministic fallback if chosen office has no eligible managers:
    - Try preferred offices first (Astana/Almaty),
    - then remaining offices ordered by office_name.
    """
    prefer_ids = {o.id for o in prefer_offices if o is not None}
    ordered = [o for o in prefer_offices if o is not None] + [
        o for o in sorted(offices, key=lambda x: (x.office_name or "")) if o.id not in prefer_ids
    ]

    for o in ordered:
        office_mgrs = [m for m in managers if m.business_unit_id == o.id]
        eligible = filter_managers(office_mgrs, needs)
        if eligible:
            return o, eligible, f"Fallback eligible office: {o.office_name}"

    return None, [], "No eligible managers in any office"


# -----------------------
# Main batch
# -----------------------
def main() -> None:
    load_dotenv(REPO_ROOT / ".env")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL missing in .env")

    ollama = OllamaClient(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b-instruct"),
    )

    engine = create_engine(db_url, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    with SessionLocal() as session:
        tickets: List[Ticket] = session.execute(select(Ticket)).scalars().all()
        offices: List[BusinessUnit] = session.execute(
            select(BusinessUnit).order_by(BusinessUnit.office_name)
        ).scalars().all()
        managers: List[Manager] = session.execute(select(Manager)).scalars().all()

        if not offices:
            raise RuntimeError("No business_units in DB. Seed offices first.")
        if not managers:
            raise RuntimeError("No managers in DB. Seed managers first.")
        if not tickets:
            print("No tickets in DB. Nothing to do.")
            return

        # Effective load starts from CSV and is updated as we assign
        effective_load: Dict[uuid.UUID, int] = {m.id: int(m.current_load) for m in managers}

        # If rerun, include already-existing assignments
        existing_assignees = session.execute(select(Assignment.manager_id)).scalars().all()
        for mid in existing_assignees:
            if mid in effective_load:
                effective_load[mid] += 1

        # Preferred offices for fallback
        astana = _find_office(offices, "астан", "astana")
        almaty = _find_office(offices, "алмат", "almaty")
        prefer_offices = [o for o in [astana, almaty] if o is not None]

        assigned_count = 0
        skipped_already_assigned = 0
        escalated_count = 0

        for t in tickets:
            # Skip if already assigned
            already = session.execute(select(Assignment).where(Assignment.ticket_id == t.id)).scalar_one_or_none()
            if already is not None:
                skipped_already_assigned += 1
                continue

            # 1) AI enrichment -> TicketAI row
            ai = enrich_ticket(session, ollama, t)
            language = (ai.language or "RU").upper()
            type_category = ai.type_category or "Консультация"

            needs = compute_needs(segment=t.segment, type_category=type_category, language=language)

            # 2) Choose office (geo rule phase 1)
            office, office_reason = choose_business_unit(t, offices)

            # 3) Filter eligible in chosen office
            office_mgrs = [m for m in managers if m.business_unit_id == office.id]
            eligible = filter_managers(office_mgrs, needs)

            # 4) Fallback: if no eligible, search other offices deterministically
            if not eligible:
                fb_office, fb_eligible, fb_reason = find_eligible_any_office(
                    offices=offices, managers=managers, needs=needs, prefer_offices=prefer_offices
                )
                if fb_office and fb_eligible:
                    office = fb_office
                    eligible = fb_eligible
                    office_reason = f"{office_reason} | {fb_reason}"
                else:
                    # Escalation (spec doesn't define, but we must not silently drop)
                    escalated_count += 1
                    # Mark AI row for review and keep going
                    ai.needs_review = True
                    session.commit()
                    print(f"[ESCALATE] ticket={t.id} segment={t.segment} type={type_category} lang={language} reason={fb_reason}")
                    continue

            # 5) Allocate (RR lock + insert assignment) in one nested transaction
            with session.begin_nested():
                allocation = allocate_round_robin(
                    session=session,
                    business_unit_id=office.id,
                    eligible=eligible,
                    needs=needs,
                    effective_load=effective_load,
                )

                trace = build_decision_trace(
                    ticket_id=t.id,
                    business_unit_id=office.id,
                    office_reason=office_reason,
                    needs=needs,
                    eligible=eligible,
                    effective_load=effective_load,
                    allocation=allocation,
                    notes=["AI enrichment via Qwen (Ollama)"],
                )

                session.add(
                    Assignment(
                        ticket_id=t.id,
                        manager_id=allocation.assigned_manager.id,
                        business_unit_id=office.id,
                        decision_trace=trace,
                    )
                )

            # Commit after each ticket (makes debugging easier)
            session.commit()

            # Update in-memory load
            effective_load[allocation.assigned_manager.id] = effective_load.get(allocation.assigned_manager.id, 0) + 1
            assigned_count += 1

        print("Batch finished.")
        print("Assigned:", assigned_count)
        print("Skipped (already assigned):", skipped_already_assigned)
        print("Escalated (no eligible anywhere):", escalated_count)


if __name__ == "__main__":
    main()
