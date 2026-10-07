"""Shared test helpers: row lookalikes, a throwaway course on disk, and answering steps.

Fake rows stand in for `sqlite3.Row` in the pure engine tests; the course
writer and `submit` drive the real engine against a real database; the
`sign_in` / `answer` / `drain` family drives the same engine through the
HTTP API.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bcrypt
import yaml

from app import curriculum, session
from app.auth import hash_password
from app.content import Program, load_program
from app.database import Database
from app.session import AnswerOutcome, record_answer


class Row(dict):
    """A dict that answers `row.keys()` and `row["col"]` like sqlite3.Row does."""

    def __missing__(self, key: str) -> Any:
        raise KeyError(key)


FAKE_HASH = "0123456789abcdef" * 4
FAKE_VERSION = "fedcba9876543210" * 4
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
    "check_epoch": 0,
    "check_version": FAKE_VERSION,
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
    "check_version": FAKE_VERSION,
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
    # Only in courses whose `lessons` layout names them.
    "tallest-mountain": {
        "prompt": "Which mountain is the tallest above sea level?",
        "answer": "Everest is the tallest mountain on Earth",
        "option": "Everest",
        "distractors": ["Denali", "Kilimanjaro", "Elbrus"],
    },
    "longest-river": {
        "prompt": "Which river is usually named the longest?",
        "answer": "The Nile is the longest river",
        "option": "Nile",
        "distractors": ["Amazon", "Danube", "Yangtze"],
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
    lessons: dict[str, list[str]] | None = None,
) -> Path:
    """Write (or rewrite) a one-chapter course under `tmp_path/content` and return its path.

    `exercises` sets the program defaults, `chapter_overrides` the chapter's own
    `exercises` block, and `cards` maps a card id to field changes (a value of
    None removes the field). Rewriting the same `tmp_path` is how a test edits
    the course between two loads. `lessons` replaces the default layout
    (lesson id -> card ids from `DEFAULT_CARDS`).
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
    for lesson_id, card_ids in (lessons or LESSONS).items():
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


# ------------------------------------------------------------ a whole learner
class Learner:
    """One learner on a course written to disk, with helpers to walk a lesson."""

    def __init__(self, db: Database, tmp_path: Path, **course):
        self.db = db
        self.tmp_path = tmp_path
        self.program = seed(db, write_program(tmp_path, **course))
        self.user = make_user(db)

    def reload(self, **course) -> Program:
        """Edit the course on disk and load it again, as a restart would."""
        self.program = seed(self.db, write_program(self.tmp_path, **course))
        return self.program

    def row(self, card_id: str):
        return self.db.card_for_user(self.user, card_id)

    def lesson(self, lesson_id: str = "first"):
        return self.db.lesson(lesson_id)

    def start(self, lesson_id: str = "first") -> list[dict]:
        self.db.start_lesson(self.user, lesson_id)
        return self.steps(lesson_id)

    def steps(self, lesson_id: str = "first") -> list[dict]:
        return session.lesson_steps(self.db, self.user, self.lesson(lesson_id))

    def answer(self, step: dict, *, right: bool = True, session_id: str = "first", **kwargs):
        row = self.row(step["card_id"])
        text = right_answer(row, step) if right else wrong_answer(row, step)
        return submit(self.db, self.user, self.program, session_id, step, text, **kwargs)

    def drain(self, lesson_id: str = "first", miss: set[str] | None = None, limit: int = 60) -> list[dict]:
        """Answer every step (right, except primaries of cards in `miss`) until the lesson ends."""
        served: list[dict] = []
        for _ in range(limit):
            queue = self.steps(lesson_id)
            if not queue:
                return served
            step = queue[0]
            served.append(step)
            wrong = step["id"].startswith("p1:") and step["card_id"] in (miss or set())
            outcome = self.answer(step, right=not wrong, session_id=lesson_id)
            assert outcome.status == "inserted", outcome
        raise AssertionError("lesson did not terminate")

    def view(self) -> dict:
        return curriculum.chapters_view(self.db, self.user, self.program, fake_now())


def lesson_ref(view: dict, lesson_id: str) -> dict:
    return next(
        lesson for chapter in view["chapters"] for lesson in chapter["lessons"] if lesson["id"] == lesson_id
    )


def first_of(queue: list[dict], card_id: str, prefix: str) -> dict:
    return next(step for step in queue if step["card_id"] == card_id and step["id"].startswith(prefix))


# ------------------------------------------------------------ the HTTP API
PASSWORD = "correct horse battery"
# A low bcrypt cost keeps sign-in fast in tests; verification accepts any cost.
_TEST_PASSWORD_HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()


def sign_in(client: Any, username: str = "learner", password: str = PASSWORD) -> Any:
    """Create an account directly in the database and sign the client in through the API."""
    password_hash = _TEST_PASSWORD_HASH if password == PASSWORD else hash_password(password)
    client.app.state.db.create_user(username, password_hash)
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def api_db(client: Any) -> Database:
    return client.app.state.db


def api_card(client: Any, card_id: str) -> dict[str, Any]:
    card = api_db(client).card(card_id)
    assert card is not None, card_id
    return card


def api_user_id(client: Any, username: str = "learner") -> str:
    user = api_db(client).get_user_by_username(username)
    assert user is not None, username
    return user["id"]


def step_event(
    session_id: str,
    step: dict[str, Any],
    text: str,
    *,
    correct: bool | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """An answer event the way the client sends it (no `id` unless replaying one on purpose)."""
    event: dict[str, Any] = {
        "session_id": session_id,
        "step_id": step["id"],
        "card_id": step["card_id"],
        "kind": step["kind"],
        "answer": text,
        "elapsed_ms": 1200,
        "timing_version": 2,
    }
    if correct is not None:
        event["correct"] = correct
    if event_id is not None:
        event["id"] = event_id
    return event


def post_answers(client: Any, events: list[dict[str, Any]]) -> dict[str, Any]:
    response = client.post("/api/answers", json={"events": events})
    assert response.status_code == 200, response.text
    return response.json()


def answer(
    client: Any,
    session_id: str,
    step: dict[str, Any],
    text: str,
    *,
    correct: bool | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Answer one step through `/api/answers`: its result plus the continuation."""
    body = post_answers(client, [step_event(session_id, step, text, correct=correct, event_id=event_id)])
    return {**body["results"][0], "session": body["session"], "state_conflict": body["state_conflict"]}


def drain(client: Any, payload: dict[str, Any], limit: int = 60) -> dict[str, Any]:
    """Answer everything correctly until the session runs out of steps."""
    session_id = payload["session_id"]
    for _ in range(limit):
        steps = payload["steps"]
        if not steps:
            return payload
        step = steps[0]
        result = answer(client, session_id, step, right_answer(api_card(client, step["card_id"]), step))
        assert result["accepted"] and not result["duplicate"], result
        payload = result["session"]
    raise AssertionError("session did not terminate")


def complete_lesson(client: Any, lesson_id: str) -> dict[str, Any]:
    response = client.post(f"/api/lessons/{lesson_id}/start")
    assert response.status_code == 200, response.text
    return drain(client, response.json())


def make_due(client: Any) -> None:
    """Move every scheduled card of every learner to yesterday."""
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with api_db(client)._transaction() as conn:
        conn.execute("UPDATE card_state SET due=? WHERE state != 'new'", (past,))
