"""Shared test helpers: row lookalikes, a throwaway course on disk, and answering steps.

Fake rows stand in for `sqlite3.Row` in the pure engine tests; the course
writer and `submit` drive the real engine against a real database.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.content import Program, load_program
from app.database import Database
from app.session import AnswerOutcome, record_answer


class Row(dict):
    """A dict that answers `row.keys()` and `row["col"]` like sqlite3.Row does."""

    def __missing__(self, key: str) -> Any:
        raise KeyError(key)


FAKE_HASH = "0123456789abcdef" * 4
FAKE_CARD_ID = "moon-landing"

CARD_DEFAULTS: dict[str, Any] = {
    "id": FAKE_CARD_ID,
    "lesson_id": "space-race",
    "chapter_id": "history",
    "position": 1,
    "retired": 0,
    "prompt": "When did the first crewed Moon landing happen?",
    "prompt_variants_json": '["On what date did people first land on the Moon?"]',
    "answer": "On 20 July 1969.",
    "option": "20 July 1969",
    "distractors_json": '["16 July 1969", "20 July 1970", "21 August 1969"]',
    "accepted_json": '["20 July 1969"]',
    "key_mode": "any_of",
    "hint": "The same summer as Woodstock.",
    "note": "Apollo 11.",
    "tags_json": "[]",
    "source": "Original text",
    "cloze_key": None,
    "cloze_options_json": "[]",
    "assemble_eligible": 0,
    "rungs_json": '["choice"]',
    "triage_enabled": 1,
    "check_hash": FAKE_HASH,
    "program_version": "test",
    "cs_state": "new",
    "cs_stability": None,
    "cs_due": None,
}

EVENT_DEFAULTS: dict[str, Any] = {
    "id": "event-1",
    "user_id": "user-1",
    "ts": "2026-10-07T10:00:00+00:00",
    "session_id": "space-race",
    "card_id": FAKE_CARD_ID,
    "kind": "choice",
    "answer": "",
    "rating": None,
    "correct": None,
    "elapsed_ms": 0,
    "timing_version": 2,
    "step_id": None,
    "check_hash": FAKE_HASH,
    "accepted_order": 1,
}


def fake_card(**overrides: Any) -> Row:
    return Row({**CARD_DEFAULTS, **overrides})


def fake_event(**overrides: Any) -> Row:
    return Row({**EVENT_DEFAULTS, **overrides})


def fake_now() -> datetime:
    """A fixed, timezone-aware moment in the middle of a learning day."""
    return datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


# ------------------------------------------------------------- a course on disk
# Three cards that support every closed exercise (choice, cloze and assemble),
# so any combination of toggles still passes the validator's exercise gate.
DEFAULT_CARDS: dict[str, dict[str, Any]] = {
    "capital-of-france": {
        "prompt": "What is the capital of France?",
        "answer": "Paris is the capital of France",
        "option": "Paris",
        "distractors": ["Lyon", "Marseille", "Nice"],
    },
    "largest-planet": {
        "prompt": "Which planet is the largest?",
        "answer": "Jupiter is the largest planet",
        "option": "Jupiter",
        "distractors": ["Saturn", "Neptune", "Mars"],
    },
    "boiling-point": {
        "prompt": "At what temperature does water boil at sea level?",
        "answer": "Water boils at one hundred degrees",
        "option": "one hundred degrees",
        "distractors": ["fifty two degrees", "two hundred degrees", "ninety nine degrees"],
    },
}
# Lesson layout of the course written by `write_program`.
LESSONS: dict[str, list[str]] = {
    "first": ["capital-of-france", "largest-planet"],
    "second": ["boiling-point"],
}


def write_program(
    tmp_path: Path,
    *,
    exercises: dict[str, bool] | None = None,
    chapter_overrides: dict[str, bool] | None = None,
    cards: dict[str, dict[str, Any]] | None = None,
) -> Path:
    """Write (or rewrite) a one-chapter course under `tmp_path/content` and return its path.

    `exercises` sets the program defaults, `chapter_overrides` the chapter's own
    `exercises` block, and `cards` maps a card id to field changes (a value of
    None removes the field). Rewriting the same `tmp_path` is how a test edits
    the course between two loads.
    """
    content = tmp_path / "content"
    (content / "chapters").mkdir(parents=True, exist_ok=True)
    program: dict[str, Any] = {
        "schema_version": 1,
        "title": "Test course",
        "language": "en",
        "chapters": ["basics"],
    }
    if exercises is not None:
        program["exercises"] = exercises
    chapter: dict[str, Any] = {"id": "basics", "title": "Basics", "lessons": []}
    if chapter_overrides is not None:
        chapter["exercises"] = chapter_overrides
    for lesson_id, card_ids in LESSONS.items():
        lesson_cards = []
        for card_id in card_ids:
            fields = {"id": card_id, **deepcopy(DEFAULT_CARDS[card_id])}
            for key, value in (cards or {}).get(card_id, {}).items():
                if value is None:
                    fields.pop(key, None)
                else:
                    fields[key] = value
            lesson_cards.append(fields)
        chapter["lessons"].append({"id": lesson_id, "title": lesson_id.title(), "cards": lesson_cards})
    (content / "program.yaml").write_text(yaml.safe_dump(program, sort_keys=False), encoding="utf-8")
    (content / "chapters" / "basics.yaml").write_text(
        yaml.safe_dump(chapter, sort_keys=False), encoding="utf-8"
    )
    return content


def seed(db: Database, content_dir: Path) -> Program:
    """Load a course from disk into the database, as application start-up does."""
    program = load_program(content_dir)
    db.upsert_program(program)
    return program


def make_user(db: Database, username: str = "learner") -> str:
    return db.create_user(username, "not-a-real-hash")


# ------------------------------------------------------------------ answering
def right_answer(row: Any, step: dict[str, Any]) -> str:
    kind = step["kind"]
    if kind == "triage":
        return "know"
    if kind == "flash":
        return "remembered"
    if kind == "choice":
        return row["option"]
    if kind == "cloze":
        return row["cloze_key"]
    return row["answer"]


def wrong_answer(row: Any, step: dict[str, Any]) -> str:
    kind = step["kind"]
    if kind == "triage":
        return "dont_know"
    if kind == "flash":
        return "again"
    if kind in ("choice", "cloze"):
        right = right_answer(row, step)
        return next(option for option in step["options"] if option != right)
    return " ".join(reversed(step["tiles"]))


def submit(
    db: Database,
    user_id: str,
    program: Program,
    session_id: str,
    step: dict[str, Any],
    text: str,
    *,
    correct: bool | None = None,
    now: datetime | None = None,
    event_id: str | None = None,
) -> AnswerOutcome:
    """Answer one step through the engine, the way the API will."""
    event = {
        "id": event_id or str(uuid.uuid4()),
        "session_id": session_id,
        "card_id": step["card_id"],
        "kind": step["kind"],
        "step_id": step["id"],
        "answer": text,
        "correct": correct,
        "elapsed_ms": 1200,
        "timing_version": 2,
    }
    return record_answer(db, user_id, event, program=program, now=now or fake_now())
