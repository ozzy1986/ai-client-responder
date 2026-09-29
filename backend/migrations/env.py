from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings

# Схема описана SQL-миграциями, автогенерация не используется — метаданные моделей не нужны.
engine = create_engine(get_settings().database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
