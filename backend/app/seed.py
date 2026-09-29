"""Загрузка базы знаний из data/kb_seed.json.

python -m app.seed           — добавить отсутствующие статьи (правки через API не затираются)
python -m app.seed --reset   — привести базу знаний точно к файлу
"""

from __future__ import annotations

import json
import sys

from app.config import get_settings
from app.domain import KBArticleIn
from app.storage import PgKnowledgeBase, make_engine


def main() -> None:
    s = get_settings()
    articles = [KBArticleIn(**a) for a in json.loads(s.kb_seed_file.read_text(encoding="utf-8"))]
    kb = PgKnowledgeBase(make_engine(s.database_url))
    changed = kb.seed(articles, reset="--reset" in sys.argv)
    print(f"kb seed: {len(articles)} in file, {changed} inserted/updated")


if __name__ == "__main__":
    main()
