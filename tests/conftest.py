import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from herjev import store as store_module
from herjev.models import Incident, Level, LogEvent

# A disposable server: tests create and drop databases and set the herjev roles' passwords.
# `docker compose up -d postgres` provides one; CI runs a postgres service container.
ADMIN_DSN = os.environ.get("HERJEV_TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/postgres")
ROLE_PASSWORDS = {"herjev_watcher": "watcher", "herjev_portal": "portal"}
# The fixtures set those well-known passwords server-wide: never on a shared server.
_host = psycopg.conninfo.conninfo_to_dict(ADMIN_DSN).get("host") or "localhost"
if _host not in ("127.0.0.1", "localhost", "::1", "postgres"):
    raise RuntimeError(f"HERJEV_TEST_DATABASE_URL must point at a local, disposable server, not {_host}")
_MIGRATE = Path(__file__).resolve().parents[1] / "scripts" / "apply-migrations.sh"


def _dsn(dbname: str, user: str | None = None) -> str:
    info = psycopg.conninfo.conninfo_to_dict(ADMIN_DSN) | {"dbname": dbname}
    if user:
        info |= {"user": user, "password": ROLE_PASSWORDS[user]}
    return psycopg.conninfo.make_conninfo(**info)


@pytest.fixture(scope="session")
def pg_template():
    """One migrated database per run; every test gets a fresh copy of it. Fails, never
    skips, when the server is unreachable: these tests are the store's only coverage."""
    name = f"herjev_tmpl_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        subprocess.run([str(_MIGRATE), _dsn(name)], check=True, capture_output=True, text=True)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role, password in ROLE_PASSWORDS.items():
                admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        yield name
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def pg_db(pg_template, monkeypatch):
    """Name of a fresh migrated database, dropped after the test. Every Store opened
    meanwhile is closed first, so no pool keeps reconnecting to a dropped database."""
    name = f"herjev_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name),
                                                                      sql.Identifier(pg_template)))
    opened: list = []
    init = store_module.Store.__init__

    def tracking_init(self, *args, **kwargs):
        init(self, *args, **kwargs)
        opened.append(self)
    monkeypatch.setattr(store_module.Store, "__init__", tracking_init)
    try:
        yield name
    finally:
        for st in opened:
            st.close()
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def pg_dsn(pg_db, monkeypatch):
    """DSN of a fresh database as the watcher role; also set as ``HERJEV_DATABASE_URL``."""
    dsn = _dsn(pg_db, "herjev_watcher")
    monkeypatch.setenv("HERJEV_DATABASE_URL", dsn)
    return dsn


@pytest.fixture
def pg_admin(pg_db):
    """Superuser connection to the test database, for setting up and inspecting rows."""
    with psycopg.connect(_dsn(pg_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def pg_portal_dsn(pg_db):
    return _dsn(pg_db, "herjev_portal")


def make_incident(level=Level.ERROR, msg="ERROR db: connection refused to 10.0.0.5:5432", count=3):
    now = time.time()
    ev = LogEvent(message=msg, level=level, source="api", host="api-1", service="api", raw=msg)
    ctx = LogEvent(message="INFO starting request 42", level=Level.INFO, source="api")
    return Incident(fingerprint="fp1", level=level, source="api", host="api-1", service="api",
                    title=msg, first_seen=now - 5, last_seen=now, count=count,
                    samples=[ev], context=[ctx, ev], id="inc1")


@pytest.fixture
def incident():
    return make_incident()


class Recorder:
    """httpx MockTransport that records requests and returns canned JSON."""

    def __init__(self, payload, status=200):
        self.payload, self.status, self.requests = payload, status, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.payload(request) if callable(self.payload) else self.payload
        return httpx.Response(self.status, json=body)

    @property
    def transport(self):
        return httpx.MockTransport(self)

    def last_json(self):
        return json.loads(self.requests[-1].content)
