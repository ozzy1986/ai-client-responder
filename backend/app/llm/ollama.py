"""LLMClient поверх Ollama /api/chat со structured output (`format` = JSON-схема)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.ports import LLMError


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_s: float = 180.0,
        temperature: float = 0.2,
        num_ctx: int = 4096,
        keep_alive: str = "-1",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.options = {"temperature": temperature, "num_ctx": num_ctx}
        # Сколько Ollama держит модель в памяти после запроса: "-1" — не выгружать (первая загрузка
        # занимает 1–2 минуты), "5m" — выгрузить через 5 минут простоя. Число Ollama ждёт числом.
        self.keep_alive: str | int = int(keep_alive) if keep_alive.lstrip("-").isdigit() else keep_alive

    def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "stream": False,
            "format": schema,
            # У «думающих» моделей (qwen3 и др.) рассуждения не нужны: схема и так задаёт шаги.
            "think": False,
            "keep_alive": self.keep_alive,
            "options": self.options,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout_s)
        except httpx.TimeoutException as e:
            raise LLMError(f"Ollama не ответила за {self.timeout_s:.0f} с") from e
        except httpx.TransportError as e:
            raise LLMError(f"Ollama недоступна по адресу {self.base_url}: {e}") from e

        if resp.status_code == 400 and "think" in resp.text:
            # Модель без поддержки think — повторяем без параметра.
            payload.pop("think")
            resp = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout_s)
        if resp.status_code == 404:
            raise LLMError(f"модель {self.model} не найдена в Ollama (ollama pull {self.model})")
        if resp.status_code >= 500:
            # Например, разовый сбой CUDA при загрузке модели на видеокарту — повтор обычно проходит.
            raise LLMError(f"Ollama вернула {resp.status_code}: {resp.text[:300]}", retryable=True)
        if resp.status_code >= 400:
            raise LLMError(f"Ollama вернула {resp.status_code}: {resp.text[:300]}")

        content = resp.json().get("message", {}).get("content", "")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError("модель вернула невалидный JSON", retryable=True) from e
        if not isinstance(data, dict):
            raise LLMError("модель вернула не JSON-объект", retryable=True)
        return data
