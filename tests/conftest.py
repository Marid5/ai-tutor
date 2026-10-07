"""Shared fixtures: a migrated scratch database, the minimal course and HTTP clients."""

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.content import Program, load_program
from app.database import Database
from app.main import create_app
from app.settings import load_settings
from tests.helpers import sign_in

FIXTURE_CONTENT = Path(__file__).parent / "fixtures" / "content_minimal"
MIGRATIONS = Path(__file__).parent.parent / "migrations"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """An empty database with every migration applied."""
    database = Database(tmp_path / "test.db")
    database.migrate(MIGRATIONS)
    return database


@pytest.fixture
def program_minimal() -> Program:
    return load_program(FIXTURE_CONTENT)


@pytest.fixture
def seeded_db(db: Database, program_minimal: Program) -> Database:
    """The scratch database with the minimal course loaded."""
    db.upsert_program(program_minimal)
    return db


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    """Build a started app on this test's database; settings fields can be overridden.

    Every client of one test shares the database file, so creating a second
    client is how a test restarts the app (optionally with other content).
    `peer` is the (host, port) the requests appear to come from.
    """
    static = tmp_path / "static"
    static.mkdir(exist_ok=True)
    stack = ExitStack()

    def factory(*, peer: tuple[str, int] = ("testclient", 50000), **overrides: Any) -> TestClient:
        fields = {
            "database_path": tmp_path / "app.db",
            "content_dir": FIXTURE_CONTENT,
            "static_dir": static,
            "cookie_secure": False,
            **overrides,
        }
        client = TestClient(create_app(replace(load_settings({}), **fields)), client=peer)
        return stack.enter_context(client)

    yield factory
    stack.close()


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    return sign_in(client)
