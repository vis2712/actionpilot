"""Test setup. Env vars are set before any `app` module is imported, so settings pick them up."""

import os
import tempfile

import psycopg
import pytest

ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL", "postgresql://actionpilot:actionpilot@localhost:5434/actionpilot")
TEST_DB = "actionpilot_test"

os.environ.update(
    LLM_PROVIDER="fake",  # deterministic notes and agent, no API calls or costs
    LLM_API_KEY="",
    STT_API_KEY="",
    JWT_SECRET="test-secret-that-is-long-enough-for-hs256-0123456789",
    DATABASE_URL=f"{ADMIN_URL.rsplit('/', 1)[0]}/{TEST_DB}",  # separate DB: tests never touch your data
    UPLOAD_DIR=tempfile.mkdtemp(prefix="actionpilot-test-"),
    QUEUE_BACKEND="background",  # run the agent in-process; no Redis needed
    EMAIL_BACKEND="console",
    WEBHOOK_SECRET="test-webhook-secret",
    ROOT_PATH="",
)


@pytest.fixture(scope="session")
def client():
    """A TestClient against a fresh `actionpilot_test` database. Skips if Postgres isn't running."""
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            conn.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable; run `docker compose up -d db redis`")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:  # `with` runs the lifespan (creates the schema)
        yield c
