from __future__ import annotations

import pytest

from app.crm.mock_amocrm import MockAmoCRM
from app.domain import Dialog, Message
from app.llm.fake import FakeLLM
from app.ports import LeadNotFound, LLMError
from app.prompts import build_user_prompt
from app.services.responder import NothingToAnswer, ResponderService
from tests.conftest import ROOT, llm_answer

crm = MockAmoCRM(ROOT / "data" / "mock_amocrm")


class ListLog:
    def __init__(self):
        self.saved = []

    def save(self, dialog, source, result) -> int:
        self.saved.append((dialog.lead_id, source, result))
        return len(self.saved)


def test_mock_amocrm_maps_roles_and_order():
    d = crm.get_dialog("30004")
    assert d.contact_name == "Дмитрий"
    assert [m.author for m in d.messages] == ["client", "manager", "client"]
    assert "Казани" in d.pending_client_messages()[0].text


def test_mock_amocrm_unknown_lead():
    with pytest.raises(LeadNotFound):
        crm.get_dialog("99999")
    with pytest.raises(LeadNotFound):
        crm.get_dialog("../../etc/passwd")


def test_prompt_separates_history_and_new_messages(memory_kb):
    d = crm.get_dialog("30004")
    prompt = build_user_prompt(d, memory_kb.search("клетчатка доставка", 3))
    history, new = prompt.split("<new_client_messages>")
    assert "[Менеджер] Здравствуйте, Дмитрий" in history
    assert "[Клиент] Нет, чай я не пью" in new
    assert "[Менеджер]" not in new
    assert "[id=" in prompt and "₽" in prompt


def test_related_products_are_added_for_upsell(memory_kb):
    svc = ResponderService(FakeLLM(), memory_kb)
    d = Dialog(lead_id="x", messages=[Message(author="client", text="клетчатка псиллиум")])
    slugs = [a.slug for a in svc.select_articles(d)]
    assert slugs[0] == "fiber-o-fiber"
    assert "probiotic-o-biotic" in slugs  # клиент о нём не спрашивал — пришёл через related_slugs


def test_analyze_happy_path_logs_result(memory_kb):
    log = ListLog()
    svc = ResponderService(FakeLLM(llm_answer(used_kb_ids=[8, 3, 777])), memory_kb, log)
    res = svc.analyze(crm.get_dialog("30004"), "amocrm_mock")
    assert res.customer_reply.startswith("Доставка")
    assert 777 not in [a.id for a in res.used_kb]
    assert any("несуществующие" in w for w in res.warnings)
    assert res.analysis_id == 1 and log.saved[0][1] == "amocrm_mock"


def test_retry_on_invalid_json_then_success(memory_kb):
    llm = FakeLLM(LLMError("bad json", retryable=True), llm_answer())
    res = ResponderService(llm, memory_kb).analyze(crm.get_dialog("30004"), "t")
    assert len(llm.calls) == 2 and res.customer_reply


def test_no_retry_when_ollama_down(memory_kb):
    llm = FakeLLM(LLMError("connection refused"), llm_answer())
    with pytest.raises(LLMError):
        ResponderService(llm, memory_kb).analyze(crm.get_dialog("30004"), "t")
    assert len(llm.calls) == 1


def test_schema_violation_is_retried_and_reported(memory_kb):
    llm = FakeLLM({"customer_reply": "hi"}, {"customer_reply": "hi"})
    with pytest.raises(LLMError, match="валидацию"):
        ResponderService(llm, memory_kb).analyze(crm.get_dialog("30004"), "t")


def test_nothing_to_answer_when_manager_spoke_last(memory_kb):
    d = Dialog(
        lead_id="x",
        messages=[Message(author="client", text="Привет"), Message(author="manager", text="Здравствуйте!")],
    )
    with pytest.raises(NothingToAnswer):
        ResponderService(FakeLLM(), memory_kb).analyze(d, "t")


def test_policy_detection_on_scenarios():
    from app.services import policy

    assert policy.detect(crm.get_dialog("30006")).health
    assert policy.detect(crm.get_dialog("30005")).complaint
    for lead in ("30001", "30002", "30004"):
        s = policy.detect(crm.get_dialog(lead))
        assert not s.health and not s.complaint, lead


def test_ollama_keep_alive_number_is_sent_as_number():
    from app.llm.ollama import OllamaClient

    assert OllamaClient("http://x", "m").keep_alive == -1
    assert OllamaClient("http://x", "m", keep_alive="5m").keep_alive == "5m"
    assert OllamaClient("http://x", "m", keep_alive="0").keep_alive == 0
