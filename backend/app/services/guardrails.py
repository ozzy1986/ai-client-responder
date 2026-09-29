"""Проверки ответа модели кодом — то, что нельзя доверить одному промпту."""

from __future__ import annotations

import re

from app.domain import KBArticle, LLMAnswer
from app.services.policy import Signals

_NO_SIGNALS = Signals()

# «1 290 ₽», «1290 руб.», «350р» — число с пробелами-разделителями тысяч перед знаком валюты.
_PRICE_RE = re.compile(r"(\d[\d\s  ]*)\s*(?:₽|руб|р\.|р\b)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d[\d\s  ]*\d|\d")
_PROMPT_TAG_RE = re.compile(
    r"</?(?:knowledge_base|client_name|dialog_history|new_client_messages|signals)>", re.IGNORECASE
)
_KB_REF_RE = re.compile(r"\s*\[id=\d+\]")
_FIELD_BLEED_RE = re.compile(r"upsell|manager_already|client_intent|used_kb", re.IGNORECASE)
_LEAK_MARKERS = (
    "id=",
    "база знаний",
    "базе знаний",
    "нашей базы",
    "нашей базе",
    "пункту ",  # «согласно пункту 10» — не путать с «пунктом выдачи»
    "допродаж",
    "подсказк",
    "upsell",
)


def _to_int(raw: str) -> int | None:
    digits = re.sub(r"\D", "", raw)
    return int(digits) if digits else None


def _known_numbers(articles: list[KBArticle]) -> set[int]:
    known: set[int] = set()
    for a in articles:
        if a.price_rub is not None:
            known.add(a.price_rub)
        for m in _NUMBER_RE.finditer(a.content):
            if (n := _to_int(m.group())) is not None:
                known.add(n)
    return known


def unknown_prices(text: str, articles: list[KBArticle], *, allow_sums: bool = False) -> list[int]:
    """Суммы в рублях из текста, которых нет в переданных статьях базы знаний.

    allow_sums — для подсказки менеджеру: там уместно «вместе выйдет 3 080 ₽» (сумма двух цен).
    """
    known = _known_numbers(articles)
    if allow_sums:
        prices = [a.price_rub for a in articles if a.price_rub is not None]
        known |= {p + q for i, p in enumerate(prices) for q in prices[i:]}
    found = [_to_int(m.group(1)) for m in _PRICE_RE.finditer(text)]
    return [n for n in found if n is not None and n not in known]


def _product_marker(a: KBArticle) -> str | None:
    """Узнаваемое слово товара: первое ключевое слово без окончания («пробиотик», «шейкер»)."""
    first = a.keywords.split()[0] if a.keywords.split() else ""
    return first[:-1].lower() if len(first) >= 5 else None


def unasked_products(reply: str, articles: list[KBArticle], dialog_text: str) -> list[str]:
    """Товары, которые упомянуты в ответе клиенту, но не встречались в переписке."""
    reply_l, dialog_l = reply.lower(), dialog_text.lower()
    return [
        a.title
        for a in articles
        if a.kind == "product"
        and (marker := _product_marker(a))
        and marker in reply_l
        and marker not in dialog_l
    ]


def apply(
    answer: LLMAnswer,
    articles: list[KBArticle],
    signals: Signals = _NO_SIGNALS,
    dialog_text: str | None = None,
) -> tuple[LLMAnswer, list[str]]:
    """Нормализует ответ и возвращает список предупреждений для менеджера."""
    warnings: list[str] = []
    needs_manager = answer.needs_manager
    given = {a.id for a in articles}

    bogus_ids = [i for i in answer.used_kb_ids if i not in given]
    if bogus_ids:
        warnings.append(f"Модель сослалась на несуществующие статьи {bogus_ids} — ссылки убраны.")
    used = [i for i in dict.fromkeys(answer.used_kb_ids) if i in given]

    hint = (answer.upsell_hint or "").strip() or None
    appropriate = answer.upsell_appropriate and hint is not None
    if appropriate and (signals.health or signals.complaint):
        warnings.append("Допродажа отключена правилом: тема здоровья или жалоба клиента.")
        appropriate = False
    if not appropriate:
        hint = None
    if signals.health and not needs_manager:
        needs_manager = True
        warnings.append("Тема здоровья — ответ помечен для проверки менеджером.")

    if bad := unknown_prices(answer.customer_reply, articles):
        warnings.append(
            f"В ответе клиенту есть суммы, которых нет в базе знаний: {bad}. Проверьте перед отправкой."
        )
    if hint and (bad := unknown_prices(hint, articles, allow_sums=True)):
        warnings.append(f"В подсказке есть суммы не из базы знаний: {bad}.")

    # Маленькие модели иногда «дописывают» разметку промпта или начинают писать в ответ клиенту
    # следующее поле схемы — вырезаем теги и id, обрезаем всё после названия поля.
    # Ссылки [id=N] убираем молча — это косметика; о вырезанных тегах и полях предупреждаем.
    raw = _KB_REF_RE.sub("", re.sub(r"[ \t]{2,}", " ", answer.customer_reply)).strip()
    reply = _PROMPT_TAG_RE.sub("", raw)
    if (m := _FIELD_BLEED_RE.search(reply)) and m.start() > 40:
        reply = reply[: m.start()]
    reply = reply.strip()
    if reply != raw:
        warnings.append("Из ответа клиенту удалена служебная разметка — перечитайте перед отправкой.")
    answer = answer.model_copy(update={"customer_reply": reply})

    if dialog_text is not None and (extra := unasked_products(reply, articles, dialog_text)):
        warnings.append(
            f"В ответе клиенту предложены товары, о которых он не спрашивал: {', '.join(extra)}. "
            "Допродажу предлагает менеджер — уберите из ответа."
        )

    lowered = answer.customer_reply.lower()
    if any(marker in lowered for marker in _LEAK_MARKERS):
        warnings.append("Ответ клиенту похож на внутреннюю пометку — перечитайте перед отправкой.")

    if not used and not needs_manager:
        needs_manager = True
        warnings.append("Ответ не опирается ни на одну статью базы знаний — нужна проверка менеджером.")

    fixed = answer.model_copy(
        update={
            "used_kb_ids": used,
            "upsell_hint": hint,
            "upsell_appropriate": appropriate,
            "needs_manager": needs_manager,
        }
    )
    return fixed, warnings
