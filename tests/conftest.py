"""Shared fixtures: a migrated scratch database and the minimal course."""

from pathlib import Path

import pytest

from app.content import Program, load_program
from app.database import Database

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
