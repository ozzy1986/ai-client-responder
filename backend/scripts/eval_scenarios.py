"""Прогон демо-сценариев на живой модели и проверка ожиданий из data/mock_amocrm/leads/*.json (_demo.expect).

    python scripts/eval_scenarios.py                    — модель из настроек
    python scripts/eval_scenarios.py --model qwen3:4b   — другая модель
    python scripts/eval_scenarios.py --runs 2           — несколько прогонов (стабильность)

Отчёт пишется в docs/eval/<модель>.md. Результаты не сохраняются в журнал analyses.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ROOT, get_settings  # noqa: E402
from app.crm.mock_amocrm import MockAmoCRM  # noqa: E402
from app.llm.ollama import OllamaClient  # noqa: E402
from app.ports import LLMError  # noqa: E402
from app.services.responder import ResponderService  # noqa: E402
from app.storage import PgKnowledgeBase, make_engine  # noqa: E402


def check(expect: dict, res) -> list[str]:
    fails = []
    for field in ("needs_manager", "upsell_appropriate"):
        if field in expect and getattr(res, field) != expect[field]:
            fails.append(f"{field}={getattr(res, field)}, ожидалось {expect[field]}")
    reply, hint = res.customer_reply.lower(), (res.upsell_hint or "").lower()
    for pat in expect.get("reply_must_mention", []):
        if not re.search(pat.lower(), reply):
            fails.append(f"в ответе нет «{pat}»")
    for pat in expect.get("reply_must_not_mention", []):
        if re.search(pat.lower(), reply):
            fails.append(f"в ответе есть недопустимое «{pat}»")
    for pat in expect.get("hint_must_mention", []):
        if not re.search(pat.lower(), hint):
            fails.append(f"в подсказке нет «{pat}»")
    for pat in expect.get("hint_must_not_mention", []):
        if re.search(pat.lower(), hint):
            fails.append(f"в подсказке есть лишнее «{pat}»")
    # Выдуманные суммы и мусор в ответе клиенту — провал; остальные предупреждения — это
    # сработавшие правила кода, они печатаются как информация.
    fails += [
        f"guardrail: {w}"
        for w in res.warnings
        if any(k in w for k in ("суммы", "пометк", "разметк", "не спрашивал"))
    ]
    return fails


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", help="id сделки")
    args = ap.parse_args()

    s = get_settings()
    model = args.model or s.llm_model
    llm = OllamaClient(
        s.ollama_url,
        model,
        timeout_s=s.llm_timeout_s,
        temperature=s.llm_temperature,
        keep_alive=s.llm_keep_alive,
    )
    svc = ResponderService(llm, PgKnowledgeBase(make_engine(s.database_url)))
    crm = MockAmoCRM(s.mock_crm_dir)

    lines = [f"# Прогон сценариев: `{model}`\n"]
    total = passed = 0
    for lead in crm.list_leads():
        if args.only and lead["lead_id"] != args.only:
            continue
        expect = crm.raw(lead["lead_id"])["_demo"]["expect"]
        dialog = crm.get_dialog(lead["lead_id"])
        for run in range(1, args.runs + 1):
            total += 1
            try:
                res = svc.analyze(dialog, "eval")
                fails = check(expect, res)
            except LLMError as e:
                res, fails = None, [f"LLMError: {e}"]
            passed += not fails
            status = "PASS" if not fails else "FAIL"
            print(
                f"[{status}] {lead['lead_id']} #{run} {lead['scenario']}"
                + (f" ({res.latency_ms / 1000:.1f} s)" if res else "")
            )
            for f in fails:
                print(f"       - {f}")
            for w in res.warnings if res else []:
                print(f"       ⚠ {w}")
            lines.append(f"## {status} · {lead['lead_id']} · {lead['scenario']} (прогон {run})\n")
            if res:
                lines += [
                    f"- **Намерение:** {res.client_intent}",
                    f"- **Менеджер уже:** {res.manager_already_did}",
                    f"- **Статьи БЗ:** {', '.join(a.slug for a in res.used_kb) or '—'}",
                    f"- **needs_manager:** {res.needs_manager} · **upsell:** {res.upsell_appropriate}"
                    f" · {res.latency_ms / 1000:.1f} с",
                    f"\n**Ответ клиенту:**\n\n> {res.customer_reply}\n",
                    f"**Подсказка менеджеру:**\n\n> {res.upsell_hint or '—'}\n",
                ]
                lines += [f"- ⚠ {w}" for w in res.warnings]
            lines += [f"- ❌ {f}" for f in fails] + [""]

    summary = f"Итого: {passed}/{total} прошли проверки"
    print(summary)
    lines.insert(1, f"{summary}\n")
    out = ROOT / "docs" / "eval" / f"{model.replace(':', '_').replace('/', '_')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"отчёт: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
