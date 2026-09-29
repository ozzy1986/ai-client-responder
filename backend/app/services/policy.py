"""Жёсткие правила по ключевым словам: темы здоровья и жалобы.

Маленькая модель иногда «забывает» правила промпта, поэтому такие случаи определяются кодом
до вызова LLM (модель получает их как сигнал) и принудительно применяются после.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain import Dialog

_HEALTH_RE = re.compile(
    r"беремен|кормл\w* грудью|кормящ|грудн\w* вскармлив|ребен|ребён|малыш|\bдет(и|ям|ей|ск)"
    r"|лекарств|препарат|таблетк|аллерг|хроническ|гастрит|язв[аеы]|диабет|давлени"
    r"|тошн|вздут|болит|боль\b|сыпь|побочн|плохо себя",
    re.IGNORECASE,
)
_COMPLAINT_RE = re.compile(
    r"протек|протёк|разбил|разбит|поврежд|брак|претензи|вернет\w* деньги|вернёт\w* деньги"
    r"|деньги верн|вернуть деньги|жалоб|обман|недовол|безобраз|неприятно|ужасн",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Signals:
    health: bool = False
    complaint: bool = False

    def for_prompt(self) -> list[str]:
        notes = []
        if self.health:
            notes.append(
                "Клиент затрагивает тему здоровья: посоветуй консультацию врача, needs_manager = true, "
                "допродажа запрещена."
            )
        if self.complaint:
            notes.append(
                "Клиент недоволен или жалуется: извинись и помоги строго по правилам из базы. "
                "Не предлагай товары, подарки и компенсации сверх правил, допродажа запрещена."
            )
        return notes


def detect(dialog: Dialog) -> Signals:
    """Смотрим на новые сообщения клиента и на предыдущие сообщения клиента в этом диалоге."""
    text = " ".join(m.text for m in dialog.messages[-8:] if m.author == "client")
    return Signals(health=bool(_HEALTH_RE.search(text)), complaint=bool(_COMPLAINT_RE.search(text)))
