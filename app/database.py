"""SQLite storage: accounts, course content and per-learner state.

Every method opens its own short-lived connection, commits on success and
closes it, so nothing is shared between requests and a crash never leaves a
half-written change behind. Times are UTC ISO-8601 strings from `utc_now()`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .content import Program
from .settings import ROOT

MIGRATIONS_DIR = ROOT / "migrations"

# Practice session ids share this prefix and nothing else does: lesson sessions
# use the lesson's own id and review sessions are `review-<day>-<slice>`. It
# lets a metrics query tell a practice event apart without a join.
PRACTICE_SESSION_ID_PREFIX = "practice-"

# Card fields whose change is worth reporting as an update. The running
# position is left out: removing one card shifts the position of every card
# after it, which is not an edit of those cards.
_CARD_IDENTITY_COLUMNS = ("id", "program_version", "position")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def check_version(check_hash: str, epoch: int) -> str:
    """Identity of one version of a card's check: its hash plus how often the hash has changed."""
    return hashlib.sha256(f"{check_hash}:{epoch}".encode()).hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass
class StoredCard:
    """A learner's schedule for one card, as kept in `card_state`."""

    due: str
    stability: float | None
    difficulty: float | None
    reps: int
    lapses: int
    state: str
    last_review: str | None


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets readers proceed while a write is in flight; it persists in the file.
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def _transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """One connection, one transaction: commit on success, roll back on error, always close.

        `immediate` takes the write lock up front, for a read-then-write sequence that
        must not see a snapshot another writer is about to change.
        """
        conn = self.connect()
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self, migrations: Path = MIGRATIONS_DIR) -> None:
        """Apply the `*.sql` files that have not run yet, each in its own transaction."""
        with self._transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY)")
        for migration in sorted(migrations.glob("*.sql")):
            conn = self.connect()
            try:
                if conn.execute("SELECT 1 FROM _migrations WHERE name=?", (migration.name,)).fetchone():
                    continue
                name = migration.name.replace("'", "''")
                try:
                    conn.executescript(
                        f"BEGIN;\n{migration.read_text(encoding='utf-8')}\n"
                        f"INSERT INTO _migrations(name) VALUES ('{name}');\nCOMMIT;"
                    )
                except BaseException:
                    conn.rollback()
                    raise
            finally:
                conn.close()

    # ------------------------------------------------------------------ users
    def create_user(self, username: str, password_hash: str) -> str:
        user_id = str(uuid.uuid4())
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                (user_id, username, password_hash, utc_now()),
            )
        return user_id

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM users WHERE username=?", (username,))

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM users WHERE id=?", (user_id,))

    def list_users(self) -> list[dict[str, Any]]:
        """Accounts for the user-management CLI; the password hash is deliberately not included."""
        rows = self.fetch_all("SELECT id,username,created_at FROM users ORDER BY created_at, username")
        return [dict(row) for row in rows]

    def set_password(self, user_id: str, password_hash: str) -> None:
        with self._transaction() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))

    def delete_user(self, user_id: str) -> None:
        """Remove the account and, by cascade, everything the learner owns."""
        with self._transaction() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))

    # ---------------------------------------------------------- sign-in sessions
    def create_auth_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                (token_hash, user_id, utc_now(), expires_at),
            )

    def auth_session_user(self, token_hash: str, now_iso: str) -> str | None:
        """The owner of a live session; a session is dead from its expiry instant on."""
        return self.scalar(
            "SELECT user_id FROM auth_sessions WHERE token_hash=? AND julianday(expires_at) > julianday(?)",
            (token_hash, now_iso),
        )

    def delete_auth_session(self, token_hash: str) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))

    def delete_user_sessions(self, user_id: str, except_hash: str | None = None) -> None:
        """Sign a learner out everywhere, optionally keeping the session making the request."""
        with self._transaction() as conn:
            if except_hash is None:
                conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
            else:
                conn.execute(
                    "DELETE FROM auth_sessions WHERE user_id=? AND token_hash != ?", (user_id, except_hash)
                )

    def purge_expired_sessions(self, now_iso: str) -> int:
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM auth_sessions WHERE julianday(expires_at) <= julianday(?)", (now_iso,)
            )
        return cursor.rowcount

    # -------------------------------------------------------------- rate limit
    def record_hit(self, bucket: str, at_iso: str) -> None:
        with self._transaction() as conn:
            conn.execute("INSERT INTO rate_limit_hits(bucket,at) VALUES(?,?)", (bucket, at_iso))

    def count_hits(self, bucket: str, since_iso: str) -> int:
        return int(
            self.scalar(
                "SELECT count(*) FROM rate_limit_hits WHERE bucket=? AND julianday(at) >= julianday(?)",
                (bucket, since_iso),
            )
        )

    def purge_hits(self, before_iso: str) -> int:
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM rate_limit_hits WHERE julianday(at) < julianday(?)", (before_iso,)
            )
        return cursor.rowcount

    # ---------------------------------------------------------------- content
    def upsert_program(self, program: Program) -> dict[str, int]:
        """Bring the stored course in line with `program`, in one transaction.

        Keyed by the stable ids, so progress survives edits to wording, titles
        and order. When a card's `check_hash` changes (its answer material was
        edited) every learner's schedule for that card is dropped and the card
        gets a new `check_version`; events stay, and readiness is derived from
        events with the current version, so the card simply starts over (also
        when an edit is reverted: the epoch only ever grows). Lesson completion is not rewritten here. Content
        missing from the program is retired, not deleted, and comes back with
        its history when the id returns.

        Returns card counts: `added`, `updated` (any stored field changed, so a
        reset or restored card with edits is counted here too), `reset`,
        `retired` and `restored`.
        """
        counts = {"added": 0, "updated": 0, "reset": 0, "retired": 0, "restored": 0}
        version = program.program_version
        with self._transaction(immediate=True) as conn:
            known = {row["id"]: row for row in conn.execute("SELECT * FROM cards")}
            chapter_ids: set[str] = set()
            lesson_ids: set[str] = set()
            card_ids: set[str] = set()
            card_position = 0
            for chapter_position, chapter in enumerate(program.chapters, start=1):
                chapter_ids.add(chapter.id)
                conn.execute(
                    """INSERT INTO chapters(id,position,title,exercises_json,retired) VALUES(?,?,?,?,0)
                    ON CONFLICT(id) DO UPDATE SET position=excluded.position,title=excluded.title,
                    exercises_json=excluded.exercises_json,retired=0""",
                    (
                        chapter.id,
                        chapter_position,
                        chapter.title,
                        _dump(program.exercises_for(chapter.id).model_dump()),
                    ),
                )
                triage = program.exercises_for(chapter.id).triage
                for lesson_position, lesson in enumerate(chapter.lessons, start=1):
                    lesson_ids.add(lesson.id)
                    conn.execute(
                        """INSERT INTO lessons(id,chapter_id,position,title,retired) VALUES(?,?,?,?,0)
                        ON CONFLICT(id) DO UPDATE SET chapter_id=excluded.chapter_id,
                        position=excluded.position,title=excluded.title,retired=0""",
                        (lesson.id, chapter.id, lesson_position, lesson.title),
                    )
                    for card in lesson.cards:
                        card_position += 1
                        card_ids.add(card.id)
                        values = {
                            "id": card.id,
                            "lesson_id": lesson.id,
                            "chapter_id": chapter.id,
                            "position": card_position,
                            "prompt": card.prompt,
                            "prompt_variants_json": _dump(card.prompt_variants),
                            "answer": card.answer,
                            "option": card.effective_option,
                            "distractors_json": _dump(card.distractors),
                            "accepted_json": _dump(card.effective_accepted),
                            "key_mode": card.key_mode,
                            "hint": card.hint,
                            "note": card.note,
                            "tags_json": _dump(card.tags),
                            "source": card.source,
                            "cloze_key": card.cloze_key,
                            "cloze_options_json": _dump(card.cloze_options),
                            "assemble_eligible": int(card.available("assemble")),
                            "rungs_json": _dump(program.rungs(chapter.id, card)),
                            "triage_enabled": int(triage),
                            "check_hash": card.check_hash(),
                            "program_version": version,
                        }
                        self._write_card(conn, values, known.get(card.id), counts)

            self._retire_missing(conn, "chapters", chapter_ids)
            self._retire_missing(conn, "lessons", lesson_ids)
            counts["retired"] = self._retire_missing(conn, "cards", card_ids)
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('program_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (version,),
            )
        return counts

    @staticmethod
    def _write_card(
        conn: sqlite3.Connection, values: dict[str, Any], previous: sqlite3.Row | None, counts: dict[str, int]
    ) -> None:
        epoch = 0
        if previous is not None:
            # Every change of the check, including a revert to an earlier text,
            # opens a new epoch, so answers given to an older version never
            # count again.
            changed = previous["check_hash"] != values["check_hash"]
            epoch = previous["check_epoch"] + (1 if changed else 0)
        values = {**values, "check_epoch": epoch, "check_version": check_version(values["check_hash"], epoch)}
        columns = list(values)
        if previous is None:
            counts["added"] += 1
        else:
            if previous["retired"]:
                counts["restored"] += 1
            if any(
                previous[key] != value for key, value in values.items() if key not in _CARD_IDENTITY_COLUMNS
            ):
                counts["updated"] += 1
            if previous["check_hash"] != values["check_hash"]:
                counts["reset"] += 1
                conn.execute("DELETE FROM card_state WHERE card_id=?", (values["id"],))
        updates = ",".join(f"{column}=excluded.{column}" for column in columns if column != "id")
        conn.execute(
            f"INSERT INTO cards({','.join(columns)},retired) VALUES({','.join('?' for _ in columns)},0) "
            f"ON CONFLICT(id) DO UPDATE SET {updates},retired=0",
            tuple(values.values()),
        )

    @staticmethod
    def _retire_missing(conn: sqlite3.Connection, table: str, present: set[str]) -> int:
        """Mark rows of `table` that the program no longer lists; returns how many changed."""
        stale = [
            row["id"]
            for row in conn.execute(f"SELECT id FROM {table} WHERE retired=0")
            if row["id"] not in present
        ]
        for row_id in stale:
            conn.execute(f"UPDATE {table} SET retired=1 WHERE id=?", (row_id,))
        return len(stale)

    def card(self, card_id: str) -> dict[str, Any] | None:
        """One card by id. Retired cards are returned too: events refer back to them."""
        return self._one("SELECT * FROM cards WHERE id=?", (card_id,))

    def cards_for_user(self, user_id: str, card_ids: list[str]) -> dict[str, sqlite3.Row]:
        """Cards joined to the learner's schedule, which the queue needs to plan."""
        if not card_ids:
            return {}
        marks = ",".join("?" for _ in card_ids)
        rows = self.fetch_all(
            f"""SELECT c.*, s.state AS cs_state, s.stability AS cs_stability, s.due AS cs_due
            FROM cards c LEFT JOIN card_state s ON s.card_id=c.id AND s.user_id=?
            WHERE c.id IN ({marks})""",
            (user_id, *card_ids),
        )
        return {row["id"]: row for row in rows}

    def card_for_user(self, user_id: str, card_id: str) -> sqlite3.Row | None:
        return self.cards_for_user(user_id, [card_id]).get(card_id)

    def reset_card_state(self, user_id: str, card_id: str) -> None:
        """Drop a schedule earned against content that no longer exists."""
        with self._transaction() as conn:
            conn.execute("DELETE FROM card_state WHERE user_id=? AND card_id=?", (user_id, card_id))

    # ------------------------------------------------------------- card state
    def get_state(self, user_id: str, card_id: str) -> StoredCard:
        """The learner's schedule for a card; a card not seen yet starts as `new`, due now."""
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO card_state(user_id,card_id,due,state,reps,lapses) "
                "VALUES(?,?,?,'new',0,0)",
                (user_id, card_id, utc_now()),
            )
            row = conn.execute(
                """SELECT due,stability,difficulty,reps,lapses,state,last_review
                FROM card_state WHERE user_id=? AND card_id=?""",
                (user_id, card_id),
            ).fetchone()
        return StoredCard(**dict(row))

    def update_state(self, user_id: str, card_id: str, state: StoredCard) -> None:
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO card_state(
                    user_id,card_id,due,stability,difficulty,reps,lapses,state,last_review)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,card_id) DO UPDATE SET due=excluded.due,stability=excluded.stability,
                difficulty=excluded.difficulty,reps=excluded.reps,lapses=excluded.lapses,state=excluded.state,
                last_review=excluded.last_review""",
                (
                    user_id,
                    card_id,
                    state.due,
                    state.stability,
                    state.difficulty,
                    state.reps,
                    state.lapses,
                    state.state,
                    state.last_review,
                ),
            )

    # ----------------------------------------------------------------- events
    def record_event(
        self, user_id: str, event: dict[str, Any]
    ) -> Literal["inserted", "duplicate_event", "duplicate_step"]:
        """Store one accepted answer; a replay of the same event or step is reported, not stored.

        `event["check_version"]` is required: it pins the answer to the version of
        the card's check that the learner actually saw.
        """
        values = {
            "timing_version": None,
            "step_id": None,
            "correct": None,
            "rating": None,
            **event,
            "check_version": event["check_version"],
            "user_id": user_id,
        }
        with self._transaction() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO events(
                    id,ts,session_id,card_id,kind,answer,rating,elapsed_ms,timing_version,step_id,correct,
                    check_version,user_id)
                VALUES(:id,:ts,:session_id,:card_id,:kind,:answer,:rating,:elapsed_ms,:timing_version,:step_id,
                    :correct,:check_version,:user_id)""",
                values,
            )
            if cursor.rowcount == 1:
                return "inserted"
            if conn.execute(
                "SELECT 1 FROM events WHERE user_id=? AND id=?", (user_id, values["id"])
            ).fetchone():
                return "duplicate_event"
            step_id = values["step_id"]
            if (
                step_id
                and conn.execute(
                    "SELECT 1 FROM events WHERE user_id=? AND step_id=?", (user_id, step_id)
                ).fetchone()
            ):
                return "duplicate_step"
            raise RuntimeError("event insert was ignored without a known idempotency conflict")

    def events_between(self, user_id: str, start: str, end: str) -> list[sqlite3.Row]:
        return self.fetch_all(
            """SELECT * FROM events WHERE user_id=?
            AND datetime(ts) >= datetime(?) AND datetime(ts) < datetime(?)
            ORDER BY datetime(ts), rowid""",
            (user_id, start, end),
        )

    def events_for_session(self, user_id: str, session_id: str) -> list[sqlite3.Row]:
        return self.fetch_all(
            "SELECT rowid AS accepted_order,* FROM events WHERE user_id=? AND session_id=? ORDER BY rowid",
            (user_id, session_id),
        )

    def events_for_user(self, user_id: str) -> list[sqlite3.Row]:
        """Accepted learning events in the order the server accepted them."""
        return self.fetch_all(
            "SELECT rowid AS accepted_order,* FROM events WHERE user_id=? ORDER BY rowid", (user_id,)
        )

    # ----------------------------------------------------------- study sessions
    def create_review_session(
        self, session_id: str, user_id: str, day: str, card_ids: list[str], slice_index: int = 0
    ) -> None:
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO study_sessions(
                    id,user_id,mode,day_key,card_ids_json,status,started_at,slice_index)
                VALUES(?,?,'scheduled_review',?,?,'in_progress',?,?)""",
                (session_id, user_id, day, _dump(card_ids), utc_now(), slice_index),
            )

    def in_progress_study_session(self, user_id: str, mode: str) -> dict[str, Any] | None:
        return self._one(
            """SELECT * FROM study_sessions WHERE user_id=? AND mode=? AND status='in_progress'
            ORDER BY started_at DESC LIMIT 1""",
            (user_id, mode),
        )

    def in_progress_practice(self, user_id: str) -> dict[str, Any] | None:
        """The one live practice session, in either mode; at most one exists."""
        return self._one(
            """SELECT * FROM study_sessions WHERE user_id=? AND status='in_progress'
            AND mode IN ('lesson_practice','mixed_practice') LIMIT 1""",
            (user_id,),
        )

    def start_practice(
        self, user_id: str, mode: str, card_ids: list[str], day: str, lesson_id: str | None = None
    ) -> dict[str, Any]:
        """Resume today's open practice of the same shape, or abandon it and start fresh.

        The check and the write are one `BEGIN IMMEDIATE` transaction so a double
        click cannot create two live practice sessions; the partial unique index
        is the backstop if a second request still slips in, handled by re-reading.
        """
        conn = self.connect()
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                active = conn.execute(
                    """SELECT * FROM study_sessions WHERE user_id=? AND status='in_progress'
                    AND mode IN ('lesson_practice','mixed_practice') LIMIT 1""",
                    (user_id,),
                ).fetchone()
                if (
                    active
                    and active["day_key"] == day
                    and active["mode"] == mode
                    and (mode != "lesson_practice" or active["lesson_id"] == lesson_id)
                ):
                    conn.execute("COMMIT")
                    return dict(active)
                if active:
                    conn.execute("UPDATE study_sessions SET status='abandoned' WHERE id=?", (active["id"],))
                session_id = f"{PRACTICE_SESSION_ID_PREFIX}{uuid.uuid4()}"
                conn.execute(
                    """INSERT INTO study_sessions(
                        id,user_id,mode,day_key,card_ids_json,status,started_at,lesson_id)
                    VALUES(?,?,?,?,?,'in_progress',?,?)""",
                    (session_id, user_id, mode, day, _dump(card_ids), utc_now(), lesson_id),
                )
                conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                existing = self.in_progress_practice(user_id)
                if existing:
                    return existing
                raise
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            return dict(conn.execute("SELECT * FROM study_sessions WHERE id=?", (session_id,)).fetchone())
        finally:
            conn.close()

    def mixed_practice_candidate_cards(self, user_id: str) -> list[sqlite3.Row]:
        """Cards of completed, live lessons that already have a schedule."""
        return self.fetch_all(
            """SELECT c.*, l.position AS lesson_position FROM cards c
            JOIN lessons l ON l.id=c.lesson_id
            JOIN user_lesson_state uls ON uls.lesson_id=l.id AND uls.user_id=? AND uls.status='completed'
            JOIN card_state s ON s.card_id=c.id AND s.user_id=? AND s.state != 'new'
            WHERE c.retired=0 AND l.retired=0
            ORDER BY l.position, c.position""",
            (user_id, user_id),
        )

    def review_sessions_of_day(self, user_id: str, day: str) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            """SELECT * FROM study_sessions WHERE user_id=? AND day_key=? AND mode='scheduled_review'
            ORDER BY slice_index""",
            (user_id, day),
        )
        return [dict(row) for row in rows]

    def study_session(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM study_sessions WHERE user_id=? AND id=?", (user_id, session_id))

    def complete_study_session(self, user_id: str, session_id: str) -> bool:
        """Only an in-progress session completes: a completed one stays put and an
        abandoned practice session is never silently reopened."""
        with self._transaction() as conn:
            cursor = conn.execute(
                """UPDATE study_sessions SET status='completed',completed_at=?
                WHERE id=? AND user_id=? AND status='in_progress'""",
                (utc_now(), session_id, user_id),
            )
        return cursor.rowcount == 1

    # ------------------------------------------------------------- curriculum
    def chapters(self) -> list[sqlite3.Row]:
        return self.fetch_all("SELECT * FROM chapters WHERE retired=0 ORDER BY position")

    def lessons_of_chapter(self, chapter_id: str) -> list[sqlite3.Row]:
        return self.fetch_all(
            "SELECT * FROM lessons WHERE chapter_id=? AND retired=0 ORDER BY position", (chapter_id,)
        )

    def lesson(self, lesson_id: str) -> dict[str, Any] | None:
        """One lesson by id. A retired lesson is returned too; check its `retired` flag."""
        return self._one("SELECT * FROM lessons WHERE id=?", (lesson_id,))

    def lesson_cards(self, lesson_id: str) -> list[sqlite3.Row]:
        return self.fetch_all(
            "SELECT * FROM cards WHERE lesson_id=? AND retired=0 ORDER BY position", (lesson_id,)
        )

    def chapter_cards(self, chapter_id: str) -> list[sqlite3.Row]:
        return self.fetch_all(
            "SELECT * FROM cards WHERE chapter_id=? AND retired=0 ORDER BY position", (chapter_id,)
        )

    def user_lesson_states(self, user_id: str) -> dict[str, sqlite3.Row]:
        rows = self.fetch_all("SELECT * FROM user_lesson_state WHERE user_id=?", (user_id,))
        return {row["lesson_id"]: row for row in rows}

    def user_lesson_state(self, user_id: str, lesson_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM user_lesson_state WHERE user_id=? AND lesson_id=?", (user_id, lesson_id)
        )

    def start_lesson(self, user_id: str, lesson_id: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO user_lesson_state(user_id,lesson_id,status,started_at)
                VALUES(?,?,'in_progress',?)
                ON CONFLICT(user_id,lesson_id) DO NOTHING""",
                (user_id, lesson_id, utc_now()),
            )

    def complete_lesson(self, user_id: str, lesson_id: str, accuracy: float) -> None:
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO user_lesson_state(user_id,lesson_id,status,completed_at,accuracy)
                VALUES(?,?,'completed',?,?)
                ON CONFLICT(user_id,lesson_id) DO UPDATE SET status='completed',
                completed_at=excluded.completed_at,accuracy=excluded.accuracy""",
                (user_id, lesson_id, utc_now(), accuracy),
            )

    def in_progress_lesson(self, user_id: str) -> str | None:
        return self.scalar(
            """SELECT s.lesson_id FROM user_lesson_state s JOIN lessons l ON s.lesson_id=l.id
            WHERE s.user_id=? AND s.status='in_progress' AND l.retired=0
            ORDER BY s.started_at, s.lesson_id LIMIT 1""",
            (user_id,),
        )

    # -------------------------------------------------------------- user meta
    def get_user_meta(self, user_id: str, key: str) -> str | None:
        return self.scalar("SELECT value FROM user_meta WHERE user_id=? AND key=?", (user_id, key))

    def set_user_meta(self, user_id: str, key: str, value: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO user_meta(user_id,key,value) VALUES(?,?,?)
                ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value""",
                (user_id, key, value),
            )

    # ------------------------------------------------------------------ utils
    def fetch_all(self, query: str, parameters: Iterable[Any] = ()) -> list[sqlite3.Row]:
        """Run a read-only query and return every row."""
        conn = self.connect()
        try:
            return conn.execute(query, tuple(parameters)).fetchall()
        finally:
            conn.close()

    def scalar(self, query: str, parameters: Iterable[Any] = ()) -> Any:
        rows = self.fetch_all(query, parameters)
        return rows[0][0] if rows else None

    def _one(self, query: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.fetch_all(query, parameters)
        return dict(rows[0]) if rows else None

    def program_version(self) -> str:
        return self.scalar("SELECT value FROM meta WHERE key='program_version'") or "unseeded"
