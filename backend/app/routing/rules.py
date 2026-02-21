# backend/app/routing/rules.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set

from app.db.models import Manager


@dataclass(frozen=True)
class RoutingNeeds:
    """Hard constraints derived from segment/type/language."""
    needs_vip: bool
    needs_chief: bool
    lang_req: str  # "RU" | "ENG" | "KZ"


def compute_needs(segment: str, type_category: str, language: str) -> RoutingNeeds:
    seg = (segment or "").strip().lower()
    typ = (type_category or "").strip().lower()
    lang = (language or "RU").strip().upper()
    if lang not in {"RU", "ENG", "KZ"}:
        lang = "RU"

    needs_vip = seg in {"vip", "priority"}
    needs_chief = typ == "смена данных"

    return RoutingNeeds(needs_vip=needs_vip, needs_chief=needs_chief, lang_req=lang)


def normalize_skills(skills: List[str]) -> Set[str]:
    return {s.strip().upper() for s in (skills or []) if s and s.strip()}


def manager_is_eligible(m: Manager, needs: RoutingNeeds) -> bool:
    skills = normalize_skills(m.skills)

    # VIP/Priority -> must have VIP skill
    if needs.needs_vip and "VIP" not in skills:
        return False

    # "Смена данных" -> only "Глав спец"
    # Note: position in your CSV is "Спец", "Ведущий спец", "Глав спец"
    pos = (m.position or "").strip().lower().replace(".", "").replace("ё", "е")
    # accept common variants
    is_chief = ("глав" in pos)  # catches "глав спец", "главный спец", "главн спец"
    if needs.needs_chief and not is_chief:
        return False

    # Language hard skills
    if needs.lang_req == "ENG" and "ENG" not in skills:
        return False
    if needs.lang_req == "KZ" and "KZ" not in skills:
        return False

    return True


def filter_managers(managers: List[Manager], needs: RoutingNeeds) -> List[Manager]:
    return [m for m in managers if m.is_active and manager_is_eligible(m, needs)]
