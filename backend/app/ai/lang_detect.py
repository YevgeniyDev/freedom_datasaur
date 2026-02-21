from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any
import re

import fasttext

# fastText language codes -> your spec codes
FT_TO_SPEC = {
    "ru": "RU",
    "en": "ENG",
    "kk": "KZ",  # Kazakh
}

_model = None

_LATIN_RE = re.compile(r"[A-Za-z]")
_CYR_RE = re.compile(r"[А-Яа-яЁё]")
# Kazakh-specific Cyrillic letters (strong KZ signal)
_KZ_LETTERS_RE = re.compile(r"[әӘөӨүҮұҰқҚғҒңҢһҺіІ]")

# Lightweight keyword hints for short/weak-confidence cases
EN_HINTS = {
    "hello", "hi", "please", "help", "blocked", "account", "verification",
    "document", "address", "upload", "unblock", "reason", "app", "error"
}
# Uzbek/translit-ish hints (so we can flag as "unknown" and default to RU)
UZ_HINTS = {"men", "siz", "ruyxat", "ruyxatdan", "utolmayapman", "nima", "nega"}

def load_lang_model(model_path: Path) -> None:
    global _model
    if _model is None:
        _model = fasttext.load_model(str(model_path))


def _script_ratios(text: str) -> Dict[str, float]:
    if not text:
        return {"latin": 0.0, "cyr": 0.0}
    latin = len(_LATIN_RE.findall(text))
    cyr = len(_CYR_RE.findall(text))
    total = max(1, latin + cyr)
    return {"latin": latin / total, "cyr": cyr / total}


def _tokenize(text: str) -> set[str]:
    # Simple whitespace tokenize; safe and fast
    return set(text.lower().replace("\n", " ").split())


def detect_language(
    text: str,
    model_path: Path,
    min_conf: float = 0.60,
    latin_eng_ratio: float = 0.60,
    low_conf_unknown: float = 0.55,
) -> Dict[str, Any]:
    """
    Returns a dict:
    {
      "final_lang": "RU"|"ENG"|"KZ",
      "final_conf": float,
      "raw_top": "ru"|"en"|"kk"|...,
      "topk": [{"code":"en","prob":0.58}, ...],
      "latin_ratio": float,
      "cyr_ratio": float,
      "unknown_flag": bool
    }

    Behavior:
    - Prefer ru/en/kk from top-k by probability.
    - If KZ letters present -> KZ (strong signal).
    - If mostly Latin and English-hints -> ENG even when p_en ~ 0.58 (your case).
    - If mostly Latin but not English-like (Uzbek/translit-ish) -> RU default + unknown_flag=True.
    - Otherwise RU default.
    """
    if not text or not text.strip():
        return {
            "final_lang": "RU",
            "final_conf": 0.0,
            "raw_top": "unknown",
            "topk": [],
            "latin_ratio": 0.0,
            "cyr_ratio": 0.0,
            "unknown_flag": False,
        }

    load_lang_model(model_path)

    # Get top-5 to be more robust for mixed texts
    labels, probs = _model.predict(text.replace("\n", " "), k=5)

    topk = []
    for lab, p in zip(labels, probs):
        code = lab.replace("__label__", "").lower()
        topk.append({"code": code, "prob": float(p)})

    raw_top = topk[0]["code"] if topk else "unknown"

    ratios = _script_ratios(text)
    latin_ratio = float(ratios["latin"])
    cyr_ratio = float(ratios["cyr"])
    tokens = _tokenize(text)

    def prob(code: str) -> float:
        for x in topk:
            if x["code"] == code:
                return float(x["prob"])
        return 0.0

    p_ru = prob("ru")
    p_en = prob("en")
    p_kk = prob("kk")

    # 0) Kazakh letters override (very strong)
    if _KZ_LETTERS_RE.search(text):
        return {
            "final_lang": "KZ",
            "final_conf": max(p_kk, 0.70),
            "raw_top": raw_top,
            "topk": topk,
            "latin_ratio": latin_ratio,
            "cyr_ratio": cyr_ratio,
            "unknown_flag": False,
        }

    # 1) Strong fastText ru/en/kk
    best_lang = "RU"
    best_p = p_ru
    if p_en > best_p:
        best_lang, best_p = "ENG", p_en
    if p_kk > best_p:
        best_lang, best_p = "KZ", p_kk

    if best_p >= min_conf:
        return {
            "final_lang": best_lang,
            "final_conf": best_p,
            "raw_top": raw_top,
            "topk": topk,
            "latin_ratio": latin_ratio,
            "cyr_ratio": cyr_ratio,
            "unknown_flag": False,
        }

    # 2) Heuristics for weak-confidence cases
    # 2a) Mostly Latin + English hints -> ENG (fixes p_en ~ 0.58 "Hello, I'm blocked..." case)
    if latin_ratio >= latin_eng_ratio and (tokens & EN_HINTS):
        return {
            "final_lang": "ENG",
            "final_conf": max(p_en, 0.58),
            "raw_top": raw_top,
            "topk": topk,
            "latin_ratio": latin_ratio,
            "cyr_ratio": cyr_ratio,
            "unknown_flag": False,
        }

    # 2b) Mostly Latin but not English-like -> unknown (Uzbek-ish), default RU per spec
    if latin_ratio >= latin_eng_ratio and (tokens & UZ_HINTS or p_en >= low_conf_unknown):
        return {
            "final_lang": "RU",
            "final_conf": max(p_ru, p_en, p_kk),
            "raw_top": raw_top,
            "topk": topk,
            "latin_ratio": latin_ratio,
            "cyr_ratio": cyr_ratio,
            "unknown_flag": True,
        }

    # 3) Default RU (spec)
    top_p = float(topk[0]["prob"]) if topk else 0.0
    return {
        "final_lang": "RU",
        "final_conf": top_p,
        "raw_top": raw_top,
        "topk": topk,
        "latin_ratio": latin_ratio,
        "cyr_ratio": cyr_ratio,
        "unknown_flag": False,
    }
