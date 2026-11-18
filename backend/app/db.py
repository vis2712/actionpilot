"""Database schema and connection pools.

Two pools: `pool` for the app (dict rows, pgvector registered) and `checkpoint_pool` for LangGraph's
checkpointer, which needs autocommit connections.
"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meetings (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         text NOT NULL,
    meeting_date  date NOT NULL,
    participants  text[] NOT NULL DEFAULT '{{}}',
    source        text NOT NULL DEFAULT 'upload',
    external_id   text,
    filename      text,
    content_type  text,
    storage_key   text,
    transcript    text,
    status        text NOT NULL DEFAULT 'queued',
    error         text,
    attempts      int NOT NULL DEFAULT 0,
    run_no        int NOT NULL DEFAULT 1,
    summary       text,
    decisions     jsonb NOT NULL DEFAULT '[]',
    notes         jsonb,
    usage         jsonb NOT NULL DEFAULT '{{}}',
    processing_ms int,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, external_id)
);
CREATE INDEX IF NOT EXISTS meetings_owner_idx ON meetings (owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tasks (
    id          bigserial PRIMARY KEY,
    owner_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    meeting_id  uuid REFERENCES meetings(id) ON DELETE CASCADE,
    item_index  int,
    title       text NOT NULL,
    assignee    text,
    due_date    date,
    priority    text NOT NULL DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high')),
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (meeting_id, item_index)
);
CREATE INDEX IF NOT EXISTS tasks_owner_idx ON tasks (owner_id, status, due_date);

CREATE TABLE IF NOT EXISTS reminders (
    id         bigserial PRIMARY KEY,
    owner_id   uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id    bigint NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    remind_at  timestamptz NOT NULL,
    status     text NOT NULL DEFAULT 'scheduled',
    sent_at    timestamptz
);

CREATE TABLE IF NOT EXISTS emails (
    id                 bigserial PRIMARY KEY,
    owner_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    meeting_id         uuid NOT NULL UNIQUE REFERENCES meetings(id) ON DELETE CASCADE,
    recipients         text[] NOT NULL,
    blocked_recipients text[] NOT NULL DEFAULT '{{}}',
    subject            text NOT NULL,
    body               text NOT NULL,
    status             text NOT NULL DEFAULT 'draft',
    error              text,
    sent_at            timestamptz,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id         bigserial PRIMARY KEY,
    owner_id   uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    meeting_id uuid NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    kind       text NOT NULL,
    content    text NOT NULL,
    embedding  vector({dim}) NOT NULL,
    tsv        tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
CREATE INDEX IF NOT EXISTS chunks_owner_idx ON chunks (owner_id);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS run_steps (
    id         bigserial PRIMARY KEY,
    meeting_id uuid NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    run_no     int NOT NULL,
    step       text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{{}}',
    ms         int,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_steps_meeting_idx ON run_steps (meeting_id, id);
"""

pool: AsyncConnectionPool | None = None
checkpoint_pool: AsyncConnectionPool | None = None


async def _configure(conn: AsyncConnection) -> None:
    await register_vector_async(conn)


async def create_schema() -> None:
    async with await AsyncConnection.connect(settings.database_url, autocommit=True) as conn:
        await conn.execute(SCHEMA.format(dim=settings.embed_dim))
    async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
        await saver.setup()  # LangGraph's own checkpoint tables


async def open_pool() -> None:
    global pool, checkpoint_pool
    pool = AsyncConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row},
        configure=_configure,
        open=False,
    )
    await pool.open(wait=True)
    checkpoint_pool = AsyncConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await checkpoint_pool.open(wait=True)


async def init_db() -> None:
    await create_schema()
    await open_pool()


async def close_db() -> None:
    global pool, checkpoint_pool
    for p in (pool, checkpoint_pool):
        if p is not None:
            await p.close()
    pool = checkpoint_pool = None


def get_pool() -> AsyncConnectionPool:
    if pool is None:
        raise RuntimeError("Database pool is not open")
    return pool


def get_checkpointer() -> AsyncPostgresSaver:
    if checkpoint_pool is None:
        raise RuntimeError("Database pool is not open")
    return AsyncPostgresSaver(checkpoint_pool)
