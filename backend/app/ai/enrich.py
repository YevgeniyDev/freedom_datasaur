# backend/app/ai/enrich.py
from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime

from sqlalchemy.orm import Session

from app.ai.schema import EnrichmentOut
from app.ai.prompts import SYSTEM_PROMPT, user_prompt
from app.ai.llm_client import OllamaClient
from app.db.models import Ticket, TicketAI
from app.ai.lang_detect import detect_language

EN_HINTS = {
    "hello", "please", "help", "blocked", "account", "verification",
    "document", "address", "upload", "unblock", "reason", "register", "registration"
}

UZ_HINTS = {
    "men", "siz", "ruyxat", "ruyxatdan", "utolmayapman", "nima", "nega",
    "iltimos", "qanday", "rahmat"
}


def enrich_ticket(session: Session, ollama: OllamaClient, ticket: Ticket) -> TicketAI:
    # If already enriched, return it
    existing = session.query(TicketAI).filter(TicketAI.ticket_id == ticket.id).one_or_none()
    if existing:
        return existing

    raw = ollama.chat_json(
        system=SYSTEM_PROMPT,
        user=user_prompt(ticket.description, ticket.attachment_path),
    )
    out = EnrichmentOut(**raw)

    # --- fastText + heuristics language decision ---
    repo_root = Path(__file__).resolve().parents[3]
    default_model = repo_root / "backend" / "app" / "ai" / "models" / "lid.176.bin"
    model_path = Path(os.getenv("FASTTEXT_LID_PATH", str(default_model)))

    text = (ticket.description or "")
    llm_lang = (out.language or "RU").upper()
    if llm_lang not in {"RU", "ENG", "KZ"}:
        llm_lang = "RU"

    try:
        det = detect_language(text, model_path=model_path)
    except Exception as e:
        det = {"ft_top": [], "latin_ratio": 0.0, "cyr_ratio": 0.0}
        out.confidence = out.confidence or {}
        out.confidence["fasttext_error"] = str(e)

    ft_top = det.get("ft_top", [])
    latin_ratio = float(det.get("latin_ratio", 0.0))

    def ft_best(code: str) -> float:
        for x in ft_top:
            if x["code"] == code:
                return float(x["prob"])
        return 0.0

    p_en = ft_best("en")
    p_ru = ft_best("ru")
    p_kk = ft_best("kk")

    # default (spec)
    final_lang = "RU"
    unknown_lang_flag = False

    # strong fastText
    if p_kk >= 0.60:
        final_lang = "KZ"
    elif p_en >= 0.60:
        final_lang = "ENG"
    elif p_ru >= 0.60:
        final_lang = "RU"
    else:
        # weak signals -> heuristics
        low_text = text.lower()
        tokens = set(low_text.replace("\n", " ").split())

        # If clearly Kazakh letters appear -> KZ
        if any(ch in low_text for ch in ["ә","ө","ү","ұ","қ","ғ","ң","һ","і"]):
            final_lang = "KZ"
        # If mostly Latin and contains common English hints -> ENG (this fixes your 0.58 case)
        elif latin_ratio >= 0.60 and (tokens & EN_HINTS):
            final_lang = "ENG"
        # If mostly Latin but NOT English-like -> treat as unknown (Uzbek-ish), keep RU but flag
        elif latin_ratio >= 0.60 and (tokens & UZ_HINTS or p_en >= 0.45):
            final_lang = "RU"
            unknown_lang_flag = True
        else:
            final_lang = "RU"

    conf = out.confidence or {}
    conf["fasttext_top5"] = ft_top
    conf["latin_ratio"] = latin_ratio
    conf["llm_language"] = {"lang": llm_lang}

    # Also store a simple summary of fastText
    conf["fasttext_summary"] = {
        "p_en": p_en, "p_ru": p_ru, "p_kk": p_kk
    }

    ai = TicketAI(
        ticket_id=ticket.id,
        type_category=out.type_category,
        sentiment=out.sentiment,
        urgency=out.urgency,
        language=final_lang,
        confidence=conf,
        needs_review=bool(out.needs_review or unknown_lang_flag),
        summary=out.summary,
        recommended_actions=out.recommended_actions,
        processed_at=datetime.utcnow(),
    )
    session.add(ai)
    session.flush()
    return ai
