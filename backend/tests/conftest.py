from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain import KBArticle

ROOT = Path(__file__).resolve().parents[2]


class MemoryKB:
    """KnowledgeBase в памяти: «поиск» — пересечение слов, этого достаточно для тестов логики."""

    def __init__(self, articles: list[KBArticle]):
        self.articles = articles

    def search(self, query: str, limit: int) -> list[KBArticle]:
        words = set(query.lower().split())
        scored = [(len(words & set(f"{a.title} {a.keywords}".lower().split())), a) for a in self.articles]
        return [a for score, a in sorted(scored, key=lambda x: -x[0]) if score > 0][:limit]

    def get_by_slugs(self, slugs: list[str]) -> list[KBArticle]:
        return [a for a in self.articles if a.slug in slugs]


@pytest.fixture
def kb_articles() -> list[KBArticle]:
    raw = json.loads((ROOT / "data" / "kb_seed.json").read_text(encoding="utf-8"))
    return [KBArticle(id=i, **a) for i, a in enumerate(raw, start=1)]


@pytest.fixture
def memory_kb(kb_articles) -> MemoryKB:
    return MemoryKB(kb_articles)


def llm_answer(**overrides) -> dict:
    base = {
        "client_intent": "Узнать стоимость доставки",
        "manager_already_did": "Менеджер назвал цену клетчатки",
        "used_kb_ids": [8],
        "needs_manager": False,
        "customer_reply": "Доставка до Казани — 350 ₽, срок 2–7 рабочих дней.",
        "upsell_appropriate": True,
        "upsell_hint": "Предложите пробиотик O-Biotic (1 790 ₽): вместе 3 080 ₽ — доставка бесплатно.",
    }
    return base | overrides
