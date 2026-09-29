"""Бизнес-логика: диалог → статьи базы знаний → LLM → проверки → журнал.

Сервис получает зависимости через конструктор и знает о них только интерфейсы из app.ports,
поэтому Ollama, amoCRM и PostgreSQL заменяются без правки этого файла.
"""

from __future__ import annotations

import logging
import time

from pydantic import ValidationError

from app.domain import AnalysisResult, Dialog, KBArticle, LLMAnswer
from app.ports import AnalysisLog, KnowledgeBase, LLMClient, LLMError
from app.prompts import ANSWER_SCHEMA, SYSTEM_PROMPT, build_user_prompt
from app.services import guardrails, policy

log = logging.getLogger(__name__)


class NothingToAnswer(Exception):
    """Последнее сообщение в диалоге — от менеджера: клиенту отвечать пока не на что."""


class ResponderService:
    def __init__(
        self,
        llm: LLMClient,
        kb: KnowledgeBase,
        analysis_log: AnalysisLog | None = None,
        *,
        search_limit: int = 4,
        max_articles: int = 7,
        attempts: int = 2,
    ):
        self.llm = llm
        self.kb = kb
        self.analysis_log = analysis_log
        self.search_limit = search_limit
        self.max_articles = max_articles
        self.attempts = attempts

    def select_articles(self, dialog: Dialog) -> list[KBArticle]:
        """Статьи под вопрос клиента + товары, которые база знаний советует предлагать вместе с ними.

        Запрос строится по сообщениям клиента с приоритетом новых: новые идут первыми и
        повторяются, чтобы не утонуть в истории. Связанные товары нужны для допродажи —
        клиент о них не спрашивает, поэтому поиском по его тексту они не находятся.
        """
        pending = dialog.pending_client_messages()
        earlier = [m for m in dialog.messages[-8:] if m.author == "client" and m not in pending]
        query = " ".join([m.text for m in pending] * 2 + [m.text for m in earlier])

        found = self.kb.search(query, self.search_limit)
        related = [s for a in found[:2] for s in a.related_slugs]
        extra = self.kb.get_by_slugs(related) if related else []

        by_id: dict[int, KBArticle] = {}
        for a in found + extra:
            by_id.setdefault(a.id, a)
        return list(by_id.values())[: self.max_articles]

    def _ask_llm(self, user_prompt: str) -> LLMAnswer:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                raw = self.llm.generate_json(SYSTEM_PROMPT, user_prompt, ANSWER_SCHEMA)
                answer = LLMAnswer.model_validate(raw)
                if not answer.customer_reply.strip():
                    raise LLMError("модель вернула пустой ответ клиенту", retryable=True)
                return answer
            except ValidationError as e:
                last_error = LLMError(f"ответ модели не прошёл валидацию: {e.error_count()} ошибок")
            except LLMError as e:
                if not e.retryable:
                    raise
                last_error = e
            log.warning("LLM attempt %s/%s failed: %s", attempt, self.attempts, last_error)
        raise LLMError(f"не удалось получить корректный ответ модели: {last_error}")

    def analyze(self, dialog: Dialog, source: str) -> AnalysisResult:
        if not dialog.pending_client_messages():
            raise NothingToAnswer("последнее сообщение в диалоге — от менеджера")

        started = time.monotonic()
        articles = self.select_articles(dialog)
        signals = policy.detect(dialog)
        answer = self._ask_llm(build_user_prompt(dialog, articles, signals.for_prompt()))
        dialog_text = " ".join(m.text for m in dialog.messages)
        answer, warnings = guardrails.apply(answer, articles, signals, dialog_text)

        used = [a for a in articles if a.id in answer.used_kb_ids]
        result = AnalysisResult(
            customer_reply=answer.customer_reply.strip(),
            upsell_hint=answer.upsell_hint,
            upsell_appropriate=answer.upsell_appropriate,
            needs_manager=answer.needs_manager,
            client_intent=answer.client_intent.strip(),
            manager_already_did=answer.manager_already_did.strip(),
            used_kb=used,
            warnings=warnings,
            model=self.llm.model,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        if self.analysis_log is not None:
            result.analysis_id = self.analysis_log.save(dialog, source, result)
        return result
