# backend/app/routing/allocator.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Assignment, Manager, RRState
from app.routing.rules import RoutingNeeds


@dataclass
class AllocationResult:
    assigned_manager: Manager
    top2: List[uuid.UUID]
    rr_last_before: Optional[uuid.UUID]
    rr_last_after: Optional[uuid.UUID]
    bucket_key: str


def make_bucket_key(business_unit_id: uuid.UUID, needs: RoutingNeeds) -> str:
    # Keep it short, deterministic, and unique per constraint-bucket
    return f"{business_unit_id}|vip:{int(needs.needs_vip)}|chief:{int(needs.needs_chief)}|lang:{needs.lang_req}"


def pick_top2_lowest_load(
    eligible: List[Manager],
    effective_load: Dict[uuid.UUID, int],
) -> List[Manager]:
    # Stable sort to avoid randomness
    return sorted(
        eligible,
        key=lambda m: (effective_load.get(m.id, m.current_load), str(m.id)),
    )[:2]


def allocate_round_robin(
    session: Session,
    business_unit_id: uuid.UUID,
    eligible: List[Manager],
    needs: RoutingNeeds,
    effective_load: Dict[uuid.UUID, int],
) -> AllocationResult:
    """
    Picks top2 lowest load, then alternates assignment between them using rr_state row lock.
    RR state is pair-specific to avoid mixing different top2 pairs over time.
    Must be called inside a transaction (session.begin()/begin_nested()).
    """
    if not eligible:
        raise ValueError("No eligible managers to allocate")

    # 1) Pick top2 FIRST (because RR should be within these two)
    top2_mgrs = pick_top2_lowest_load(eligible, effective_load)
    top2_ids = [m.id for m in top2_mgrs]

    # 2) Pair-specific bucket key (sorted ids)
    if len(top2_ids) == 1:
        pair_key = f"{top2_ids[0]}"
    else:
        a, b = sorted([str(top2_ids[0]), str(top2_ids[1])])
        pair_key = f"{a}:{b}"

    bucket_key = f"{business_unit_id}|vip:{int(needs.needs_vip)}|chief:{int(needs.needs_chief)}|lang:{needs.lang_req}|pair:{pair_key}"

    # 3) Lock (or create) rr_state row for this *pair*
    rr_row = session.execute(
        select(RRState).where(RRState.bucket_key == bucket_key).with_for_update()
    ).scalar_one_or_none()

    if rr_row is None:
        rr_row = RRState(bucket_key=bucket_key, last_manager_id=None, updated_at=datetime.utcnow())
        session.add(rr_row)
        session.flush()

    rr_last_before = rr_row.last_manager_id

    # 4) Choose via RR within the current top2
    if len(top2_mgrs) == 1:
        chosen = top2_mgrs[0]
    else:
        m1, m2 = top2_mgrs[0], top2_mgrs[1]
        chosen = m2 if rr_last_before == m1.id else m1

    rr_row.last_manager_id = chosen.id
    rr_row.updated_at = datetime.utcnow()

    return AllocationResult(
        assigned_manager=chosen,
        top2=top2_ids,
        rr_last_before=rr_last_before,
        rr_last_after=chosen.id,
        bucket_key=bucket_key,
    )
