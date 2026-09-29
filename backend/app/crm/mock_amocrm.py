"""Mock amoCRM: сделки и переписка из JSON-файлов в формате, близком к ответам amoCRM API v4.

Файл data/mock_amocrm/leads/<id>.json содержит:
  - "lead"     — как GET /api/v4/leads/{id}?with=contacts (id, name, price, _embedded.contacts);
  - "messages" — сообщения чата; автор задаётся как в amoCRM: author.type = "contact" (клиент)
                 или "user" (сотрудник).
Адаптер отвечает только за перевод этого формата в доменный Dialog. Реальный клиент amoCRM
реализует тот же интерфейс CRMSource (см. docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain import Dialog, Message
from app.ports import LeadNotFound

_AUTHOR_MAP = {"contact": "client", "user": "manager"}


class MockAmoCRM:
    def __init__(self, data_dir: Path):
        self.leads_dir = data_dir / "leads"

    def _load(self, lead_id: str) -> dict[str, Any]:
        if not lead_id.isdigit():
            raise LeadNotFound(lead_id)
        path = self.leads_dir / f"{lead_id}.json"
        if not path.is_file():
            raise LeadNotFound(lead_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def raw(self, lead_id: str) -> dict[str, Any]:
        """Сырой «ответ amoCRM» — показываем в демо, чтобы было видно, что приходит на вход."""
        return self._load(lead_id)

    def list_leads(self) -> list[dict[str, Any]]:
        leads = []
        for path in sorted(self.leads_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            leads.append(
                {
                    "lead_id": str(data["lead"]["id"]),
                    "title": data["lead"]["name"],
                    "scenario": data.get("_demo", {}).get("scenario", ""),
                }
            )
        return leads

    def get_dialog(self, lead_id: str) -> Dialog:
        data = self._load(lead_id)
        lead = data["lead"]
        contacts = lead.get("_embedded", {}).get("contacts", [])
        messages = [
            Message(
                author=_AUTHOR_MAP[m["author"]["type"]],
                text=m["text"],
                created_at=datetime.fromtimestamp(m["created_at"], tz=UTC),
            )
            for m in sorted(data["messages"], key=lambda m: m["created_at"])
        ]
        return Dialog(
            lead_id=str(lead["id"]),
            contact_name=contacts[0]["name"] if contacts else None,
            lead_title=lead.get("name"),
            messages=messages,
        )
