# scripts/seed_db.py
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, List

import pandas as pd

# Ensure repo root is on sys.path so "backend.app..." imports work when run from scripts/
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.app.db.session import db_session  # noqa: E402
from backend.app.db.models import BusinessUnit, Manager, Ticket  # noqa: E402


DATA_DIR = REPO_ROOT / "data"
BUSINESS_UNITS_CSV = DATA_DIR / "business_units.csv"
MANAGERS_CSV = DATA_DIR / "managers.csv"
TICKETS_CSV = DATA_DIR / "tickets.csv"


def _clean_str(x) -> Optional[str]:
    if pd.isna(x):
        return None
    s = str(x).strip()
    return s if s else None


def _norm_key(s: Optional[str]) -> Optional[str]:
    """Normalize strings for robust matching (trim, collapse spaces, casefold)."""
    if not s:
        return None
    return " ".join(str(s).strip().split()).casefold()


def _parse_skills(raw) -> List[str]:
    """
    Accepts formats like:
      "VIP, ENG, KZ"
      "VIP;ENG"
      "['VIP','ENG']"
      NaN
    Returns normalized list like ["VIP","ENG","KZ"] (uppercase, unique, stable order).
    """
    if pd.isna(raw) or raw is None:
        return []

    s = str(raw).strip()
    if not s:
        return []

    # Handle python-like list string
    if s.startswith("[") and s.endswith("]"):
        s = s.strip("[]")
    # Replace common separators with commas
    s = s.replace(";", ",").replace("|", ",")
    parts = [p.strip().strip("'").strip('"') for p in s.split(",") if p.strip()]
    out: List[str] = []
    seen = set()
    for p in parts:
        up = p.upper()
        if up and up not in seen:
            seen.add(up)
            out.append(up)
    return out


def main() -> None:
    if not BUSINESS_UNITS_CSV.exists():
        raise FileNotFoundError(f"Missing {BUSINESS_UNITS_CSV}")
    if not MANAGERS_CSV.exists():
        raise FileNotFoundError(f"Missing {MANAGERS_CSV}")
    if not TICKETS_CSV.exists():
        raise FileNotFoundError(f"Missing {TICKETS_CSV}")

    # --- Load CSVs ---
    bu_df = pd.read_csv(BUSINESS_UNITS_CSV)
    mgr_df = pd.read_csv(MANAGERS_CSV)
    tix_df = pd.read_csv(TICKETS_CSV)

    # Strip column names (IMPORTANT: fixes "Должность " bug)
    bu_df.columns = [c.replace("\ufeff","").strip() for c in bu_df.columns]
    mgr_df.columns = [c.replace("\ufeff","").strip() for c in mgr_df.columns]
    tix_df.columns = [c.replace("\ufeff","").strip() for c in tix_df.columns]

    # --- Expected column names ---
    # business_units.csv: "Офис", "Адрес"
    bu_office_col = "Офис"
    bu_address_col = "Адрес"

    # managers.csv (your actual headers): "ФИО", "Должность", "Офис", "Навыки", "Количество обращений в работе"
    mgr_name_col = "ФИО"
    mgr_position_col = "Должность"
    mgr_office_col = "Офис"
    mgr_skills_col = "Навыки"
    mgr_load_col = "Количество обращений в работе"

    # tickets.csv
    tix_guid_col = "GUID клиента"
    tix_gender_col = "Пол клиента"
    tix_dob_col = "Дата рождения"
    tix_segment_col = "Сегмент клиента"
    tix_desc_col = "Описание"
    tix_attach_col = "Вложения"
    tix_country_col = "Страна"
    tix_region_col = "Область"
    tix_city_col = "Населённый пункт"
    tix_street_col = "Улица"
    tix_house_col = "Дом"

    # --- Seed into DB ---
    with db_session() as db:
        db.execute("TRUNCATE assignments, rr_state, ticket_ai, tickets, managers, business_units;")
        # 1) Business Units
        # Use normalized office name for robust mapping
        office_to_bu_id = {}
        for _, row in bu_df.iterrows():
            office_name_raw = _clean_str(row.get(bu_office_col))
            office_key = _norm_key(office_name_raw)
            if not office_name_raw or not office_key:
                continue

            address = _clean_str(row.get(bu_address_col))

            existing = db.query(BusinessUnit).filter(BusinessUnit.office_name == office_name_raw).one_or_none()
            if existing:
                if address and not existing.address:
                    existing.address = address
                office_to_bu_id[office_key] = existing.id
            else:
                bu = BusinessUnit(office_name=office_name_raw, address=address)
                db.add(bu)
                db.flush()  # get bu.id
                office_to_bu_id[office_key] = bu.id

        # 2) Managers
        missing_offices = set()

        for _, row in mgr_df.iterrows():
            full_name = _clean_str(row.get(mgr_name_col))
            position = _clean_str(row.get(mgr_position_col))
            if position:
                position = " ".join(position.split()).replace("ё", "е")
            office_name_raw = _clean_str(row.get(mgr_office_col))
            office_key = _norm_key(office_name_raw)

            if not full_name or not position or not office_name_raw or not office_key:
                continue

            bu_id = office_to_bu_id.get(office_key)
            if not bu_id:
                # Business unit not found: try exact lookup; if absent, create BU on the fly
                bu = db.query(BusinessUnit).filter(BusinessUnit.office_name == office_name_raw).one_or_none()
                if not bu:
                    bu = BusinessUnit(office_name=office_name_raw, address=None)
                    db.add(bu)
                    db.flush()
                bu_id = bu.id
                office_to_bu_id[office_key] = bu_id
                missing_offices.add(office_name_raw)

            skills = _parse_skills(row.get(mgr_skills_col))
            try:
                current_load = int(row.get(mgr_load_col)) if not pd.isna(row.get(mgr_load_col)) else 0
            except Exception:
                current_load = 0

            # Upsert by (full_name, business_unit_id)
            existing = (
                db.query(Manager)
                .filter(Manager.full_name == full_name, Manager.business_unit_id == bu_id)
                .one_or_none()
            )
            if existing:
                existing.position = position
                existing.skills = skills
                existing.current_load = current_load
                existing.is_active = True
            else:
                m = Manager(
                    full_name=full_name,
                    position=position,
                    skills=skills,
                    business_unit_id=bu_id,
                    current_load=current_load,
                    is_active=True,
                )
                db.add(m)

        # 3) Tickets
        for _, row in tix_df.iterrows():
            client_guid = _clean_str(row.get(tix_guid_col))
            gender = _clean_str(row.get(tix_gender_col))
            segment = _clean_str(row.get(tix_segment_col)) or "Mass"
            description = _clean_str(row.get(tix_desc_col))
            attachment_path = _clean_str(row.get(tix_attach_col))

            country = _clean_str(row.get(tix_country_col))
            region = _clean_str(row.get(tix_region_col))
            city = _clean_str(row.get(tix_city_col))
            street = _clean_str(row.get(tix_street_col))
            house = _clean_str(row.get(tix_house_col))

            birth_date = None
            dob_raw = row.get(tix_dob_col)
            if dob_raw is not None and not pd.isna(dob_raw):
                try:
                    birth_date = pd.to_datetime(dob_raw, errors="coerce").date()
                except Exception:
                    birth_date = None

            t = Ticket(
                client_guid=client_guid,
                gender=gender,
                birth_date=birth_date,
                segment=segment,
                description=description,
                attachment_path=attachment_path,
                country=country,
                region=region,
                city=city,
                street=street,
                house=house,
            )
            db.add(t)

    print("✅ Seed complete.")
    print(f"Loaded: {len(bu_df)} business_units rows, {len(mgr_df)} managers rows, {len(tix_df)} tickets rows.")
    if missing_offices:
        print("⚠️ Managers referenced offices not present in business_units.csv (created on the fly):")
        for name in sorted(missing_offices):
            print("  -", name)


if __name__ == "__main__":
    main()
    