from __future__ import annotations

from app.domain import LLMAnswer
from app.services import guardrails
from tests.conftest import llm_answer


def by_slug(articles, *slugs):
    return [a for a in articles if a.slug in slugs]


def test_prices_from_kb_pass(kb_articles):
    arts = by_slug(kb_articles, "delivery", "fiber-o-fiber", "probiotic-o-biotic")
    ans, warnings = guardrails.apply(LLMAnswer(**llm_answer()), arts)
    assert warnings == []
    assert ans.upsell_hint and ans.upsell_appropriate


def test_invented_price_is_flagged(kb_articles):
    arts = by_slug(kb_articles, "delivery")
    ans = LLMAnswer(**llm_answer(customer_reply="Доставка стоит 499 руб., придёт завтра."))
    _, warnings = guardrails.apply(ans, arts)
    assert any("499" in w for w in warnings)


def test_price_formats(kb_articles):
    arts = by_slug(kb_articles, "fiber-o-fiber")
    assert guardrails.unknown_prices("Цена 1 290 ₽", arts) == []
    assert guardrails.unknown_prices("Цена 1290 руб", arts) == []
    assert guardrails.unknown_prices("Цена 1 290 ₽", arts) == []
    assert guardrails.unknown_prices("Цена 1 390 ₽", arts) == [1390]


def test_sum_of_prices_allowed_only_in_hint(kb_articles):
    arts = by_slug(kb_articles, "fiber-o-fiber", "probiotic-o-biotic")
    assert guardrails.unknown_prices("вместе 3 080 ₽", arts, allow_sums=True) == []
    assert guardrails.unknown_prices("вместе 3 080 ₽", arts) == [3080]


def test_hint_dropped_when_upsell_inappropriate(kb_articles):
    ans = LLMAnswer(**llm_answer(upsell_appropriate=False, upsell_hint="Предложите шейкер"))
    fixed, _ = guardrails.apply(ans, kb_articles)
    assert fixed.upsell_hint is None and not fixed.upsell_appropriate


def test_empty_hint_means_no_upsell(kb_articles):
    fixed, _ = guardrails.apply(LLMAnswer(**llm_answer(upsell_hint="  ")), kb_articles)
    assert fixed.upsell_hint is None and not fixed.upsell_appropriate


def test_prompt_tags_are_stripped(kb_articles):
    ans = LLMAnswer(**llm_answer(customer_reply="Посоветуйтесь с врачом.  <new_client_messages>"))
    fixed, warnings = guardrails.apply(ans, kb_articles)
    assert fixed.customer_reply == "Посоветуйтесь с врачом."
    assert any("разметка" in w for w in warnings)


def test_internal_leak_in_customer_reply(kb_articles):
    ans = LLMAnswer(**llm_answer(customer_reply="Согласно базе знаний [id=8], доставка 350 ₽."))
    _, warnings = guardrails.apply(ans, kb_articles)
    assert any("внутреннюю" in w for w in warnings)


def test_field_bleed_is_cut(kb_articles):
    reply = "Доставка до Казани — 350 ₽, срок 2–7 рабочих дней. [id=8] Upsell_hint: Предложите пробиотик"
    fixed, warnings = guardrails.apply(LLMAnswer(**llm_answer(customer_reply=reply)), kb_articles)
    assert fixed.customer_reply == "Доставка до Казани — 350 ₽, срок 2–7 рабочих дней."
    assert warnings


def test_policy_signals_override_model(kb_articles):
    from app.services.policy import Signals

    fixed, warnings = guardrails.apply(
        LLMAnswer(**llm_answer(needs_manager=False)), kb_articles, Signals(health=True)
    )
    assert fixed.needs_manager and not fixed.upsell_appropriate and fixed.upsell_hint is None
    assert len(warnings) == 2


def test_no_kb_means_manager(kb_articles):
    fixed, _ = guardrails.apply(LLMAnswer(**llm_answer(used_kb_ids=[])), kb_articles)
    assert fixed.needs_manager


def test_unasked_product_in_reply_is_flagged(kb_articles):
    reply = "Приносим извинения! Заменим флакон, а также можем отправить шейкер O-complex."
    ans = LLMAnswer(**llm_answer(customer_reply=reply, used_kb_ids=[10]))
    _, warnings = guardrails.apply(ans, kb_articles, dialog_text="Флакон с хлорофиллом протёк")
    assert any("Шейкер" in w for w in warnings)


def test_products_from_dialog_are_not_flagged(kb_articles):
    reply = "В набор входят клетчатка O-Fiber, фиточай и хлорофилл."
    ans = LLMAnswer(**llm_answer(customer_reply=reply))
    dialog = "В «Детокс 21» входят клетчатка O-Fiber, фиточай «Лёгкость», хлорофилл O-Green"
    _, warnings = guardrails.apply(ans, kb_articles, dialog_text=dialog)
    assert not any("не спрашивал" in w for w in warnings)


def test_article_number_reference_is_leak_but_plain_word_is_not(kb_articles):
    leak = LLMAnswer(**llm_answer(customer_reply="Согласно пункту 10, пришлите фото повреждения."))
    ok = LLMAnswer(**llm_answer(customer_reply="Согласно пункту правил возврата, пришлите фото."))
    assert any("пометк" in w for w in guardrails.apply(leak, kb_articles)[1])
    assert not any("пометк" in w for w in guardrails.apply(ok, kb_articles)[1])
