"""kb_articles + analyses

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE kb_articles (
            id            serial PRIMARY KEY,
            slug          text NOT NULL UNIQUE,
            kind          text NOT NULL CHECK (kind IN ('product','service','policy','faq','company')),
            title         text NOT NULL,
            content       text NOT NULL,
            price_rub     integer CHECK (price_rub >= 0),
            keywords      text NOT NULL DEFAULT '',
            related_slugs text[] NOT NULL DEFAULT '{}',
            -- ё → е и в индексе, и в запросе (app.storage.to_or_query), чтобы «всё» = «все».
            search tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('russian', translate(title,    'ёЁ', 'еЕ')), 'A') ||
                setweight(to_tsvector('russian', translate(keywords, 'ёЁ', 'еЕ')), 'B') ||
                setweight(to_tsvector('russian', translate(content,  'ёЁ', 'еЕ')), 'C')
            ) STORED,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX kb_articles_search_idx ON kb_articles USING gin (search);

        CREATE TABLE analyses (
            id          bigserial PRIMARY KEY,
            lead_id     text NOT NULL,
            source      text NOT NULL,
            dialog      jsonb NOT NULL,
            kb_ids      integer[] NOT NULL DEFAULT '{}',
            result      jsonb NOT NULL,
            warnings    jsonb NOT NULL DEFAULT '[]',
            model       text NOT NULL,
            latency_ms  integer NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX analyses_lead_idx ON analyses (lead_id, id DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE analyses; DROP TABLE kb_articles;")
