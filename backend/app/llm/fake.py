"""Детерминированная «модель» для тестов: отдаёт заранее заданные ответы по очереди."""

from __future__ import annotations

from typing import Any

from app.ports import LLMError


class FakeLLM:
    model = "fake"

    def __init__(self, *responses: dict[str, Any] | Exception):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user))
        if not self.responses:
            raise LLMError("FakeLLM: ответы закончились")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
