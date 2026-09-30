"""HTTP-слой: валидация входа, вызов сервиса, перевод исключений в HTTP-коды. Логики здесь нет."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app.config import get_settings
from app.crm.mock_amocrm import MockAmoCRM
from app.domain import AnalysisResult, Dialog, KBArticle, KBArticleIn, Message
from app.llm.ollama import OllamaClient
from app.ports import LeadNotFound, LLMError
from app.ratelimit import AnalyzeGate, ModelBusy, RateLimited
from app.services.responder import NothingToAnswer, ResponderService
from app.storage import ArticleExists, PgAnalysisLog, PgKnowledgeBase, make_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("acr")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    engine = make_engine(s.database_url)
    if s.llm_provider != "ollama":
        raise RuntimeError(f"неизвестный LLM_PROVIDER={s.llm_provider}")
    if s.crm_provider != "mock":
        raise RuntimeError(f"неизвестный CRM_PROVIDER={s.crm_provider}")

    app.state.kb = PgKnowledgeBase(engine)
    app.state.analyses = PgAnalysisLog(engine)
    app.state.crm = MockAmoCRM(s.mock_crm_dir)
    app.state.llm = OllamaClient(
        s.ollama_url, s.llm_model, timeout_s=s.llm_timeout_s, temperature=s.llm_temperature
    )
    app.state.responder = ResponderService(app.state.llm, app.state.kb, app.state.analyses)
    app.state.gate = AnalyzeGate(s.analyze_per_ip_per_hour)
    yield
    engine.dispose()


app = FastAPI(title="AI Client Responder", version="1.0.0", lifespan=lifespan)


@app.exception_handler(LLMError)
def _llm_error(_: Request, e: LLMError) -> JSONResponse:
    log.error("LLM error: %s", e)
    return JSONResponse({"detail": f"LLM: {e}"}, status_code=503)


# --- служебное ---


@app.get("/api/healthz")
def healthz(request: Request) -> dict[str, str]:
    request.app.state.analyses.ping()
    return {"status": "ok"}


@app.get("/api/status")
def status(request: Request) -> dict[str, Any]:
    """Для баннера в интерфейсе: доступна ли Ollama и скачана ли модель."""
    llm: OllamaClient = request.app.state.llm
    info: dict[str, Any] = {"model": llm.model, "ollama_url": llm.base_url}
    try:
        tags = httpx.get(f"{llm.base_url}/api/tags", timeout=3).json()
        names = {m["name"] for m in tags.get("models", [])}
        info["llm_reachable"] = True
        info["model_available"] = llm.model in names or f"{llm.model}:latest" in names
    except (httpx.HTTPError, ValueError):
        info["llm_reachable"] = False
        info["model_available"] = False
    return info


# --- CRM (mock amoCRM) ---


@app.get("/api/leads")
def list_leads(request: Request) -> list[dict[str, Any]]:
    return request.app.state.crm.list_leads()


@app.get("/api/leads/{lead_id}")
def get_lead(lead_id: str, request: Request) -> dict[str, Any]:
    crm = request.app.state.crm
    try:
        return {"dialog": crm.get_dialog(lead_id), "raw": crm.raw(lead_id)}
    except LeadNotFound:
        raise HTTPException(404, f"сделка {lead_id} не найдена") from None


# --- анализ ---


class AnalyzeRequest(BaseModel):
    """Сделка из CRM (lead_id) и/или диалог целиком (messages — например, из вебхука или
    отредактированный в демо). Если переданы messages, история из CRM не запрашивается."""

    lead_id: str | None = Field(default=None, max_length=32)
    messages: list[Message] | None = Field(default=None, max_length=100)
    contact_name: str | None = Field(default=None, max_length=100)
    new_client_message: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _has_source(self) -> AnalyzeRequest:
        if self.lead_id is None and not self.messages:
            raise ValueError("укажите lead_id или messages")
        return self


@app.post("/api/analyze")
def analyze(body: AnalyzeRequest, request: Request) -> AnalysisResult:
    if body.messages:
        dialog = Dialog(
            lead_id=body.lead_id or "manual", contact_name=body.contact_name, messages=body.messages
        )
        source = "manual"
    else:
        try:
            dialog: Dialog = request.app.state.crm.get_dialog(body.lead_id)
        except LeadNotFound:
            raise HTTPException(404, f"сделка {body.lead_id} не найдена") from None
        source = "amocrm_mock"

    if body.new_client_message and body.new_client_message.strip():
        extra = Message(author="client", text=body.new_client_message.strip())
        dialog = dialog.model_copy(update={"messages": [*dialog.messages, extra]})

    ip = request.client.host if request.client else "unknown"
    try:
        with request.app.state.gate.slot(ip):
            return request.app.state.responder.analyze(dialog, source)
    except NothingToAnswer as e:
        raise HTTPException(409, f"нечего отвечать: {e}") from None
    except ModelBusy:
        raise HTTPException(429, "модель сейчас отвечает на другой запрос — повторите через минуту") from None
    except RateLimited as e:
        raise HTTPException(
            429,
            f"лимит анализов с одного адреса исчерпан — повторите через {e.retry_after_s // 60 + 1} мин",
            headers={"Retry-After": str(e.retry_after_s)},
        ) from None


@app.get("/api/leads/{lead_id}/saved-analysis")
def saved_analysis(lead_id: str, request: Request) -> dict[str, Any]:
    """Последний настоящий ответ модели по сделке — показываем, когда модель выключена."""
    row = request.app.state.analyses.latest_for_lead(lead_id)
    if row is None:
        raise HTTPException(404, "по этой сделке ещё нет сохранённого анализа")
    return {
        **row["result"],
        "used_kb": request.app.state.kb.get_by_ids(row["kb_ids"]),
        "analysis_id": row["id"],
        "model": row["model"],
        "latency_ms": row["latency_ms"],
        "created_at": row["created_at"],
        "saved": True,
    }


@app.get("/api/analyses")
def recent_analyses(request: Request, limit: int = 20) -> list[dict[str, Any]]:
    return request.app.state.analyses.recent(min(max(limit, 1), 100))


# --- база знаний ---


@app.get("/api/kb")
def kb_list(request: Request) -> list[KBArticle]:
    return request.app.state.kb.list()


@app.get("/api/kb/search")
def kb_search(q: str, request: Request, limit: int = 5) -> list[KBArticle]:
    """Отладка поиска: какие статьи попадут в промпт для такого текста."""
    return request.app.state.kb.search(q, min(max(limit, 1), 20))


@app.post("/api/kb", status_code=201)
def kb_create(body: KBArticleIn, request: Request) -> KBArticle:
    try:
        return request.app.state.kb.create(body)
    except ArticleExists:
        raise HTTPException(409, f"статья со slug «{body.slug}» уже есть") from None


@app.put("/api/kb/{article_id}")
def kb_update(article_id: int, body: KBArticleIn, request: Request) -> KBArticle:
    try:
        art = request.app.state.kb.update(article_id, body)
    except ArticleExists:
        raise HTTPException(409, f"статья со slug «{body.slug}» уже есть") from None
    if art is None:
        raise HTTPException(404, "статья не найдена")
    return art


@app.delete("/api/kb/{article_id}", status_code=204)
def kb_delete(article_id: int, request: Request) -> None:
    if not request.app.state.kb.delete(article_id):
        raise HTTPException(404, "статья не найдена")


# Демо-страница. Монтируется последней, чтобы не перекрывать /api.
app.mount("/", StaticFiles(directory=get_settings().frontend_dir, html=True), name="frontend")
