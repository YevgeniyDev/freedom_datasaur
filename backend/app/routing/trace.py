# backend/app/routing/trace.py
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from app.db.models import Manager
from app.routing.allocator import AllocationResult
from app.routing.rules import RoutingNeeds


def build_decision_trace(
    *,
    ticket_id: uuid.UUID,
    business_unit_id: uuid.UUID,
    office_reason: str,
    needs: RoutingNeeds,
    eligible: List[Manager],
    effective_load: Dict[uuid.UUID, int],
    allocation: AllocationResult,
    notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    elig_rows = []
    for m in eligible:
        elig_rows.append(
            {
                "manager_id": str(m.id),
                "full_name": m.full_name,
                "position": m.position,
                "skills": m.skills,
                "effective_load": int(effective_load.get(m.id, m.current_load)),
                "base_load_csv": int(m.current_load),
            }
        )

    return {
        "ticket_id": str(ticket_id),
        "business_unit_id": str(business_unit_id),
        "office_reason": office_reason,
        "needs": {
            "needs_vip": needs.needs_vip,
            "needs_chief": needs.needs_chief,
            "lang_req": needs.lang_req,
        },
        "eligible_count": len(eligible),
        "eligible_managers": elig_rows,
        "top2_ids": [str(x) for x in allocation.top2],
        "rr": {
            "bucket_key": allocation.bucket_key,
            "last_before": str(allocation.rr_last_before) if allocation.rr_last_before else None,
            "last_after": str(allocation.rr_last_after) if allocation.rr_last_after else None,
        },
        "assigned_manager_id": str(allocation.assigned_manager.id),
        "notes": notes or [],
    }
