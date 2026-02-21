from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal

Category = Literal[
    "Жалоба",
    "Смена данных",
    "Консультация",
    "Претензия",
    "Неработоспособность приложения",
    "Мошеннические действия",
    "Спам",
]
Sentiment = Literal["Позитивный", "Нейтральный", "Негативный"]
Lang = Literal["RU", "ENG", "KZ"]

class EnrichmentOut(BaseModel):
    type_category: Category
    sentiment: Sentiment
    urgency: int = Field(ge=1, le=10)
    language: Lang = "RU"
    summary: str
    recommended_actions: List[str] = Field(default_factory=list)
    confidence: Dict[str, Any] = Field(default_factory=dict)
    needs_review: bool = False
    