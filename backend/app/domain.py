"""Доменные модели. Ничего не знают ни про Ollama, ни про amoCRM, ни про SQL."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Author = Literal["client", "manager"]


class Message(BaseModel):
    author: Author
    text: str = Field(min_length=1, max_length=4000)
    created_at: datetime | None = None


class Dialog(BaseModel):
    """Диалог по сделке — то, что адаптер CRM приводит к общему виду."""

    lead_id: str
    contact_name: str | None = None
    lead_title: str | None = None
    messages: list[Message] = Field(min_length=1, max_length=100)

    def pending_client_messages(self) -> list[Message]:
        """Сообщения клиента после последнего ответа менеджера — то, на что отвечаем."""
        pending: list[Message] = []
        for msg in reversed(self.messages):
            if msg.author != "client":
                break
            pending.append(msg)
        return list(reversed(pending))


class KBArticle(BaseModel):
    id: int
    slug: str
    kind: Literal["product", "service", "policy", "faq", "company"]
    title: str
    content: str
    price_rub: int | None = None
    keywords: str = ""
    related_slugs: list[str] = []


class KBArticleIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{2,64}$")
    kind: Literal["product", "service", "policy", "faq", "company"]
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=2, max_length=4000)
    price_rub: int | None = Field(default=None, ge=0)
    keywords: str = ""
    related_slugs: list[str] = []


class LLMAnswer(BaseModel):
    """Ответ модели. Порядок полей = порядок генерации: сначала модель разбирает ситуацию,
    потом пишет ответ клиенту и только затем — внутреннюю подсказку менеджеру."""

    client_intent: str
    manager_already_did: str
    used_kb_ids: list[int]
    needs_manager: bool
    customer_reply: str
    upsell_appropriate: bool
    upsell_hint: str | None


class AnalysisResult(BaseModel):
    customer_reply: str
    upsell_hint: str | None
    upsell_appropriate: bool
    needs_manager: bool
    client_intent: str
    manager_already_did: str
    used_kb: list[KBArticle]
    warnings: list[str]
    model: str
    latency_ms: int
    analysis_id: int | None = None
