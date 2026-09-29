"""Интерфейсы, от которых зависит бизнес-логика. Реализации: llm/, crm/, storage/."""

from __future__ import annotations

from typing import Any, Protocol

from app.domain import AnalysisResult, Dialog, KBArticle


class LLMError(Exception):
    """LLM недоступна или вернула мусор. `retryable` — имеет ли смысл повторить запрос."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LLMClient(Protocol):
    model: str

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Вернуть объект, соответствующий JSON-схеме, или бросить LLMError."""
        ...


class LeadNotFound(Exception):
    pass


class CRMSource(Protocol):
    def list_leads(self) -> list[dict[str, Any]]:
        """Краткий список сделок для выбора в интерфейсе."""
        ...

    def get_dialog(self, lead_id: str) -> Dialog:
        """История переписки по сделке в доменном виде. LeadNotFound — если сделки нет."""
        ...


class KnowledgeBase(Protocol):
    def search(self, query: str, limit: int) -> list[KBArticle]: ...

    def get_by_slugs(self, slugs: list[str]) -> list[KBArticle]: ...


class AnalysisLog(Protocol):
    def save(self, dialog: Dialog, source: str, result: AnalysisResult) -> int: ...
