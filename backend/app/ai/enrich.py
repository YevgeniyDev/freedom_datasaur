# backend/app/ai/enrich.py
from __future__ import annotations

import os
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Tuple, Optional

from sqlalchemy.orm import Session

from app.ai.schema import EnrichmentOut
from app.ai.prompts import SYSTEM_PROMPT, user_prompt
from app.ai.llm_client import OllamaClient
from app.db.models import Ticket, TicketAI
from app.ai.lang_detect import detect_language

# OCR deps (Step 2)
from PIL import Image
import pytesseract


EN_HINTS = {
    "hello", "please", "help", "blocked", "account", "verification",
    "document", "address", "upload", "unblock", "reason", "register", "registration",
}

UZ_HINTS = {
    "men", "siz", "ruyxat", "ruyxatdan", "utolmayapman", "nima", "nega",
    "iltimos", "qanday", "rahmat",
}

# -----------------------
# Rule-based guardrails
# -----------------------
CHANGE_DATA_PATTERNS = [
    r"\bсмен(а|ить)\b.*\bданн",
    r"\bобнов(ить|ление)\b.*\bданн",
    r"\bизмен(ить|ение)\b.*\b(телефон|номер|почт|email|e-mail|адрес|паспорт|удост|иин|фио|фамил|имя|дата рождения)\b",
    r"\bпомен(ять|ял)\b.*\b(телефон|номер|почт|email|e-mail|адрес|паспорт|удост|иин|фио|фамил|имя|дата рождения)\b",
    r"\bисправ(ить|ление)\b.*\b(фио|фамил|имя|дата рождения|паспорт|удост|иин)\b",
]

# Important: verification/registration issues are APP ISSUES, not "Смена данных"
APP_FLOW_PATTERNS = [
    r"\bне могу\b.*\b(подтверд(ить|ить)|верифицирова(ть|ться)|подтвержден)\b",
    r"\b(верификац|подтвержден|подтвердить)\b.*\b(адрес|регистрац)\b",
    r"\bрегистрац(ия|ионн)\b.*\b(адрес|подтвердить)\b",
    r"\bязык\b.*\b(документ|справк|выписк)\b",
    r"\bдокумент\b.*\b(dia|диа)\b",
    r"\bне проход(ит|ит)\b.*\b(верификац|регистрац|подтвержден)\b",
]

PAYMENT_FAIL_PATTERNS = [
    r"\bне (могу|удается)\b.*\b(оплат|провест(и|ь))\b",
    r"\bоплат(а|ить)\b.*\bне проход(ит|ит)\b",
    r"\bошибк(а|и)\b.*\b(оплат|платеж)\b",
    r"\b(карта|картой)\b.*\bне (работает|принимается|проходит)\b",
    r"\bотклонен\b.*\bплатеж\b",
]

COMMISSION_FEE_PATTERNS = [
    r"\bкомисс(ия|ию)\b",
    r"\bудерж(ан|ива)ется\b",
    r"\bсписан(ие|о)\b",
    r"\bобслужив(ание|анию)\b",
    r"\bтариф(ы|ов|ный)\b",
    r"\bбездействующ(их|его)\s+счет",
]

SPAM_PATTERNS = [
    r"http[s]?://",
    r"www\.",
    r"t\.me/",
    r"@\w+",
    r"\b(заработ(ок|ай)|доход|инвестиц|крипт|bitcoin|btc|ставк|казино|bet)\b",
    r"\b(розыгрыш|выиграл|приз|бонус)\b",
]


def rule_override_category(text: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Deterministic overrides to prevent damaging misclassification:
    - Spam -> Спам
    - App flow (verification/registration/doc language) -> Неработоспособность приложения
    - Payment fail -> Неработоспособность приложения
    - Strong personal data change -> Смена данных
    - Fees/commission questions -> Консультация
    """
    t = (text or "").strip().lower()
    signals: Dict[str, Any] = {"hit": []}
    if not t:
        return None, signals

    # 1) Spam first
    for p in SPAM_PATTERNS:
        if re.search(p, t, re.IGNORECASE):
            signals["hit"].append(f"spam:{p}")
            return "Спам", signals

    # 2) App flow failures must win over "Смена данных"
    for p in APP_FLOW_PATTERNS:
        if re.search(p, t, re.IGNORECASE):
            signals["hit"].append(f"app_flow:{p}")
            return "Неработоспособность приложения", signals

    # 3) Payment failures -> app issue
    for p in PAYMENT_FAIL_PATTERNS:
        if re.search(p, t, re.IGNORECASE):
            signals["hit"].append(f"payment_fail:{p}")
            return "Неработоспособность приложения", signals

    # 4) Strong personal data change
    for p in CHANGE_DATA_PATTERNS:
        if re.search(p, t, re.IGNORECASE):
            signals["hit"].append(f"change_data:{p}")
            return "Смена данных", signals

    # 5) Fee/commission questions are consultation (not change-data)
    fee_hits = sum(1 for p in COMMISSION_FEE_PATTERNS if re.search(p, t, re.IGNORECASE))
    if fee_hits >= 2:
        signals["hit"].append({"fee_hits": fee_hits})
        return "Консультация", signals

    return None, signals


def _clamp_urgency(u: Any, default: int = 5) -> int:
    try:
        v = int(u)
    except Exception:
        v = int(default)
    return max(1, min(10, v))


# -----------------------
# OCR helper (Step 2)
# -----------------------
def resolve_attachment_path(rel_or_abs: str | None) -> Optional[Path]:
    """
    tickets.csv stores relative paths like 'order_error.png'.
    We resolve them against repo_root/data/.
    Also supports absolute paths.
    """
    if not rel_or_abs:
        return None
    s = str(rel_or_abs).strip()
    if not s:
        return None

    p = Path(s)
    if p.is_absolute() and p.exists():
        return p

    # repo root: backend/app/ai/enrich.py -> parents[3] == repo root
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "data" / p.name
    if candidate.exists():
        return candidate

    # also allow direct relative from repo root
    candidate2 = repo_root / p
    if candidate2.exists():
        return candidate2

    return None


def ocr_attachment_text(attachment: str | None) -> Optional[str]:
    p = resolve_attachment_path(attachment)
    if not p or not p.is_file():
        return None

    if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None

    try:
        img = Image.open(p)
        # rus+eng is enough for your screenshots; can add kaz later if installed
        text = pytesseract.image_to_string(img, lang="rus+eng")
        text = " ".join((text or "").split())
        return text if text else None
    except Exception:
        return None


def enrich_ticket(session: Session, ollama: OllamaClient, ticket: Ticket) -> TicketAI:
    # If already enriched, return it
    existing = session.query(TicketAI).filter(TicketAI.ticket_id == ticket.id).one_or_none()
    if existing:
        return existing

    # OCR extracted text (used in prompt; safe even if None)
    attachment_ocr = ocr_attachment_text(ticket.attachment_path)

    raw = ollama.chat_json(
        system=SYSTEM_PROMPT,
        user=user_prompt(ticket.description, ticket.attachment_path, attachment_ocr),
    )
    out = EnrichmentOut(**raw)

    # -----------------------
    # Category rule overrides (text-only, but OCR helps too because it gets added to description below)
    # -----------------------
    # Combine description + OCR for robust rules
    combined_text = (ticket.description or "")
    if attachment_ocr:
        combined_text = combined_text + "\n\n[OCR]\n" + attachment_ocr

    override_cat, rule_signals = rule_override_category(combined_text)
    if override_cat:
        out.type_category = override_cat
        out.confidence = out.confidence or {}
        out.confidence["rule_override"] = {"type_category": override_cat, **rule_signals}

    # -----------------------
    # VIP/Priority urgency boost (business rule)
    # -----------------------
    u = _clamp_urgency(out.urgency, default=5)
    seg = (ticket.segment or "").strip().lower()
    if seg in {"vip", "priority"}:
        u = max(u, 8)
    out.urgency = u

    # -----------------------
    # fastText + heuristics language decision
    # -----------------------
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

    ft_top = det.get("ft_top", []) or []
    latin_ratio = float(det.get("latin_ratio", 0.0) or 0.0)

    def ft_best(code: str) -> float:
        for x in ft_top:
            if x.get("code") == code:
                try:
                    return float(x.get("prob"))
                except Exception:
                    return 0.0
        return 0.0

    p_en = ft_best("en")
    p_ru = ft_best("ru")
    p_kk = ft_best("kk")

    # Defaults per spec
    final_lang = "RU"
    unknown_lang_flag = False

    # Strong fastText
    if p_kk >= 0.60:
        final_lang = "KZ"
    elif p_en >= 0.60:
        final_lang = "ENG"
    elif p_ru >= 0.60:
        final_lang = "RU"
    else:
        low_text = text.lower()
        tokens = set(low_text.replace("\n", " ").split())

        if any(ch in low_text for ch in ["ә", "ө", "ү", "ұ", "қ", "ғ", "ң", "һ", "і"]):
            final_lang = "KZ"
        elif latin_ratio >= 0.60 and (tokens & EN_HINTS):
            final_lang = "ENG"
        elif latin_ratio >= 0.60 and ((tokens & UZ_HINTS) or p_en >= 0.45):
            final_lang = "RU"
            unknown_lang_flag = True
        else:
            final_lang = "RU"

    # If fastText top1 is a non-known language with decent prob, flag unknown (still route RU)
    top1 = ft_top[0] if ft_top else None
    top_code = (top1.get("code") if isinstance(top1, dict) else None)
    try:
        top_prob = float(top1.get("prob")) if isinstance(top1, dict) and top1.get("prob") is not None else 0.0
    except Exception:
        top_prob = 0.0

    KNOWN = {"ru", "en", "kk"}
    if top_code and (top_code not in KNOWN) and top_prob >= 0.55 and latin_ratio >= 0.45:
        unknown_lang_flag = True

    # If language seems unknown, annotate summary for manager/UI
    if unknown_lang_flag:
        note = (
            "⚠️ Похоже, язык обращения не RU/ENG/KZ. "
            "По регламенту маршрутизируем как RU, но требуется проверка."
        )
        base = (out.summary or "").strip()
        out.summary = (base + " " + note).strip()

    # Confidence enrichment
    conf = out.confidence or {}
    conf["fasttext_top5"] = ft_top
    conf["fasttext_top1"] = {"code": top_code, "prob": top_prob}
    conf["latin_ratio"] = latin_ratio
    conf["llm_language"] = {"lang": llm_lang}
    conf["fasttext_summary"] = {"p_en": p_en, "p_ru": p_ru, "p_kk": p_kk}
    conf["unknown_language_flag"] = bool(unknown_lang_flag)

    # Store OCR snippet in confidence (useful for debugging/UI)
    if attachment_ocr:
        conf["attachment_ocr"] = attachment_ocr[:500]  # keep it small

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
