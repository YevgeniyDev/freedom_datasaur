import httpx
import json
from typing import Any, Dict

class OllamaClient:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat_json(self, system: str, user: str) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": 0},
            "stream": False,
        }
        r = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=60)
        r.raise_for_status()
        content = r.json()["message"]["content"].strip()

        # Try parse JSON directly
        try:
            return json.loads(content)
        except Exception:
            # Try a repair pass: ask model to output JSON only
            repair_payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "Верни ТОЛЬКО валидный JSON. Никакого текста вокруг."},
                    {"role": "user", "content": content},
                ],
                "options": {"temperature": 0},
                "stream": False,
            }
            rr = httpx.post(f"{self.base_url}/api/chat", json=repair_payload, timeout=60)
            rr.raise_for_status()
            fixed = rr.json()["message"]["content"].strip()
            return json.loads(fixed)
        