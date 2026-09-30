"""PostgreSQL: база знаний (CRUD + полнотекстовый поиск) и журнал анализов.

Поиск — встроенный full-text PostgreSQL (словарь russian со стеммингом), без векторной БД:
база знаний — десятки статей, а стемминг и веса (заголовок > ключевые слова > текст)
находят нужные статьи надёжно и объяснимо.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from app.domain import AnalysisResult, Dialog, KBArticle, KBArticleIn

_COLUMNS = "id, slug, kind, title, content, price_rub, keywords, related_slugs"
_WORD_RE = re.compile(r"[a-zа-яё0-9]{3,}")


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2)


def _row(r: Any) -> KBArticle:
    return KBArticle(**r._mapping)


# Вежливые и служебные слова чата: встроенный стоп-лист russian их не знает, а они
# дают ложные совпадения с длинными статьями.
_CHAT_STOPWORDS = {
    "добрый",
    "доброе",
    "день",
    "вечер",
    "утро",
    "здравствуйте",
    "привет",
    "подскажите",
    "скажите",
    "пожалуйста",
    "спасибо",
    "можно",
    "хочу",
    "хотим",
    "хотела",
    "хотел",
    "тогда",
    "очень",
    "вообще",
    "ваш",
    "ваша",
    "ваши",
    "вашу",
    "вас",
    "нас",
    "мне",
}


def to_or_query(query: str, max_words: int = 60) -> str:
    """Текст клиента → tsquery «слово1 | слово2 | …». Пропускаем только буквы и цифры,
    поэтому спецсимволы tsquery в запрос попасть не могут."""
    words = _WORD_RE.findall(query.lower().replace("ё", "е"))
    words = list(dict.fromkeys(w for w in words if w not in _CHAT_STOPWORDS))
    return " | ".join(words[:max_words])


class ArticleExists(Exception):
    pass


class PgKnowledgeBase:
    def __init__(self, engine: Engine):
        self.engine = engine

    # --- поиск (интерфейс KnowledgeBase) ---

    def search(self, query: str, limit: int) -> list[KBArticle]:
        tsq = to_or_query(query)
        if not tsq:
            return []
        sql = text(
            f"""
            SELECT {_COLUMNS}
            FROM kb_articles, to_tsquery('russian', :q) AS q
            WHERE search @@ q
            -- нормировка 1: делим на 1 + log(длины), чтобы длинные статьи не выигрывали объёмом
            ORDER BY ts_rank_cd(search, q, 1) DESC, id
            LIMIT :limit
            """
        )
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(sql, {"q": tsq, "limit": limit})]

    def get_by_slugs(self, slugs: list[str]) -> list[KBArticle]:
        sql = text(f"SELECT {_COLUMNS} FROM kb_articles WHERE slug = ANY(:slugs)")
        with self.engine.connect() as c:
            rows = {r.slug: _row(r) for r in c.execute(sql, {"slugs": slugs})}
        return [rows[s] for s in dict.fromkeys(slugs) if s in rows]

    def get_by_ids(self, ids: list[int]) -> list[KBArticle]:
        sql = text(f"SELECT {_COLUMNS} FROM kb_articles WHERE id = ANY(:ids)")
        with self.engine.connect() as c:
            rows = {r.id: _row(r) for r in c.execute(sql, {"ids": ids})}
        return [rows[i] for i in ids if i in rows]

    # --- CRUD ---

    def list(self) -> list[KBArticle]:
        sql = text(f"SELECT {_COLUMNS} FROM kb_articles ORDER BY kind, title")
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(sql)]

    def get(self, article_id: int) -> KBArticle | None:
        sql = text(f"SELECT {_COLUMNS} FROM kb_articles WHERE id = :id")
        with self.engine.connect() as c:
            r = c.execute(sql, {"id": article_id}).first()
        return _row(r) if r else None

    def create(self, data: KBArticleIn) -> KBArticle:
        sql = text(
            f"""
            INSERT INTO kb_articles (slug, kind, title, content, price_rub, keywords, related_slugs)
            VALUES (:slug, :kind, :title, :content, :price_rub, :keywords, :related_slugs)
            ON CONFLICT (slug) DO NOTHING
            RETURNING {_COLUMNS}
            """
        )
        with self.engine.begin() as c:
            r = c.execute(sql, data.model_dump()).first()
        if r is None:
            raise ArticleExists(data.slug)
        return _row(r)

    def update(self, article_id: int, data: KBArticleIn) -> KBArticle | None:
        sql = text(
            f"""
            UPDATE kb_articles SET slug = :slug, kind = :kind, title = :title, content = :content,
                   price_rub = :price_rub, keywords = :keywords, related_slugs = :related_slugs,
                   updated_at = now()
            WHERE id = :id
            RETURNING {_COLUMNS}
            """
        )
        try:
            with self.engine.begin() as c:
                r = c.execute(sql, {**data.model_dump(), "id": article_id}).first()
        except IntegrityError:
            raise ArticleExists(data.slug) from None
        return _row(r) if r else None

    def delete(self, article_id: int) -> bool:
        with self.engine.begin() as c:
            res = c.execute(text("DELETE FROM kb_articles WHERE id = :id"), {"id": article_id})
        return res.rowcount > 0

    def seed(self, articles: list[KBArticleIn], *, reset: bool = False) -> int:
        """Загрузить статьи. Без reset существующие (по slug) не трогаем — правки через CRUD сохраняются."""
        conflict = (
            """DO UPDATE SET kind = EXCLUDED.kind, title = EXCLUDED.title, content = EXCLUDED.content,
               price_rub = EXCLUDED.price_rub, keywords = EXCLUDED.keywords,
               related_slugs = EXCLUDED.related_slugs, updated_at = now()"""
            if reset
            else "DO NOTHING"
        )
        sql = text(
            f"""
            INSERT INTO kb_articles (slug, kind, title, content, price_rub, keywords, related_slugs)
            VALUES (:slug, :kind, :title, :content, :price_rub, :keywords, :related_slugs)
            ON CONFLICT (slug) {conflict}
            """
        )
        with self.engine.begin() as c:
            if reset:
                c.execute(
                    text("DELETE FROM kb_articles WHERE NOT (slug = ANY(:slugs))"),
                    {"slugs": [a.slug for a in articles]},
                )
            return sum(c.execute(sql, a.model_dump()).rowcount for a in articles)


class PgAnalysisLog:
    def __init__(self, engine: Engine):
        self.engine = engine

    def save(self, dialog: Dialog, source: str, result: AnalysisResult) -> int:
        sql = text(
            """
            INSERT INTO analyses (lead_id, source, dialog, kb_ids, result, warnings, model, latency_ms)
            VALUES (:lead_id, :source, CAST(:dialog AS jsonb), :kb_ids, CAST(:result AS jsonb),
                    CAST(:warnings AS jsonb), :model, :latency_ms)
            RETURNING id
            """
        )
        payload = result.model_dump(mode="json", exclude={"used_kb", "analysis_id"})
        with self.engine.begin() as c:
            return c.execute(
                sql,
                {
                    "lead_id": dialog.lead_id,
                    "source": source,
                    "dialog": dialog.model_dump_json(),
                    "kb_ids": [a.id for a in result.used_kb],
                    "result": json.dumps(payload, ensure_ascii=False),
                    "warnings": json.dumps(result.warnings, ensure_ascii=False),
                    "model": result.model,
                    "latency_ms": result.latency_ms,
                },
            ).scalar_one()

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        sql = text(
            """
            SELECT id, lead_id, source, model, latency_ms, created_at,
                   result->>'customer_reply' AS customer_reply,
                   result->>'upsell_hint' AS upsell_hint,
                   (result->>'needs_manager')::bool AS needs_manager,
                   jsonb_array_length(warnings) AS warnings_count
            FROM analyses ORDER BY id DESC LIMIT :limit
            """
        )
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(sql, {"limit": limit})]

    def latest_for_lead(self, lead_id: str) -> dict[str, Any] | None:
        """Последний анализ сделки в исходном виде из CRM (без правок в демо) — для показа,
        когда модель недоступна."""
        sql = text(
            """
            SELECT id, result, kb_ids, model, latency_ms, created_at
            FROM analyses WHERE lead_id = :lead_id AND source = 'amocrm_mock'
            ORDER BY id DESC LIMIT 1
            """
        )
        with self.engine.connect() as c:
            r = c.execute(sql, {"lead_id": lead_id}).first()
        return dict(r._mapping) if r else None

    def ping(self) -> None:
        with self.engine.connect() as c:
            c.execute(text("SELECT 1"))
