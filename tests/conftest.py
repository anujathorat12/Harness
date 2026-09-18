from __future__ import annotations

import os
import tempfile
import uuid

# CRITICAL: these env vars must be set before ANY `harness.*` module is
# imported anywhere in the test session, because harness/core/config.py
# instantiates a module-level `settings` singleton at import time, and
# harness/core/database.py builds its engine from that singleton at import
# time too. conftest.py is imported by pytest before test modules are
# collected, so this is the one place that's guaranteed to run first.
_TEST_DIR = tempfile.mkdtemp(prefix="harness-tests-")
os.environ["HARNESS_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DIR}/test.db"
os.environ["HARNESS_RUNTIME_WORKDIR_ROOT"] = f"{_TEST_DIR}/sandboxes"
os.environ["HARNESS_APPROVAL_DEFAULT_TIMEOUT_SECONDS"] = "5"  # keep timeout tests fast

import pytest
import pytest_asyncio

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Match harness/core/config.py's dev defaults exactly -- tests don't override
# these env vars, so `settings.api_key_*` resolves to these values.
ADMIN_KEY = "dev-admin-key-***CHANGE-ME***"
OPERATOR_KEY = "dev-operator-key-***CHANGE-ME***"
AUDITOR_KEY = "dev-auditor-key-***CHANGE-ME***"
CLIENT_KEY = "dev-client-key-***CHANGE-ME***"


def agent_script(*parts: str) -> str:
    return os.path.join(REPO_ROOT, *parts)


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _init_db():
    from harness.core.database import init_db

    await init_db()
    yield


def _make_client(headers: dict[str, str]):
    import httpx

    from harness.main import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30, headers=headers)


@pytest_asyncio.fixture
async def app_client():
    """One shared app/DB across the whole test session (see the env-var
    note above for why); each test uses unique() names so agents/policies
    from different tests never collide. Defaults to the ADMIN role so every
    pre-existing test (written before auth existed) keeps passing unchanged;
    tests that specifically exercise authorization use the role-specific
    fixtures below instead."""
    async with _make_client({"X-API-Key": ADMIN_KEY}) as client:
        yield client


@pytest_asyncio.fixture
async def operator_client():
    async with _make_client({"X-API-Key": OPERATOR_KEY}) as client:
        yield client


@pytest_asyncio.fixture
async def auditor_client():
    async with _make_client({"X-API-Key": AUDITOR_KEY}) as client:
        yield client


@pytest_asyncio.fixture
async def client_client():
    async with _make_client({"X-API-Key": CLIENT_KEY}) as client:
        yield client


@pytest_asyncio.fixture
async def anon_client():
    async with _make_client({}) as client:
        yield client
