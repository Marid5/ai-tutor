"""Steps: what a single exercise looks like, who may answer it, and whether the answer was right.

Five kinds of step exist:

    triage    first meeting: prompt and answer, the learner says "know" or "don't know"
    flash     a learning step: flip the card, self-grade
    choice    pick the right answer among four buttons
    cloze     the key is cut out of the answer and filled back in from options
    assemble  the answer is rebuilt from its own shuffled words

The last three are *closed*: the server can check them. A card's *rungs* are
the closed kinds it climbs, in the order above, limited to the kinds its
chapter enables and its own data supports; they are computed when the course
is loaded and stored on the card row. The first rung is the card's *primary
check*. Only primary checks decide whether a card is known.

Invariants this module upholds:

- The verdict on a closed step is recomputed here from the answer text. The
  client's own `correct` flag is never evidence.
- Each card row carries a `check_version`: a fingerprint of its answer
  material plus an epoch that grows on every change of that material, so an
  edit that is later reverted still yields a new version.
- Every step id carries the first 8 hex characters of the check version
  (`h8`). After an edit new steps never collide with old ones, and an answer
  to a step issued before the edit no longer matches the card: it is refused
  as stale.
- Everything derived from history (the ladder, readiness, "already checked")
  reads only events recorded under the card's current check version, so an
  edited card starts over and old answers are never re-graded against new
  content.

Nothing here touches the database; rows and events only need `row["column"]`.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from .content import find_key_span, normalize_text

STEP_KINDS = {"triage", "choice", "flash", "cloze", "assemble"}
# Server-verified. Only these count towards readiness: a self-assessment
# cannot be checked, so it cannot be evidence that a card is known.
CLOSED_KINDS = {"choice", "cloze", "assemble"}
LEARNING_KINDS = {"triage", "flash"}

# The answers a self-graded step accepts; anything else is a malformed event.
TRIAGE_ANSWERS = {"know", "dont_know"}
FLASH_ANSWERS = {"remembered", "again"}

# Step-id prefixes. They are load-bearing, not decoration: readiness, the
# idempotency indexes and the ladder all read them.
PRIMARY_PREFIX = "p1:"  # the one primary check of a card in a session
LADDER_PREFIX = "v2:"  # a ladder rung, identified by the miss that opened it
STEP_ID_MAX = 300
HASH_PREFIX_LENGTH = 8

MATURE_STABILITY_DAYS = 21.0


# ---------------------------------------------------------------- card data
def _has(row: Any, column: str) -> bool:
    # sqlite3.Row answers `in` against its values, so the key check needs `keys()`.
    return column in row.keys()  # noqa: SIM118


def _json_list(row: Any, column: str) -> list[str]:
    raw = row[column] if _has(row, column) else None
    return json.loads(raw) if raw else []


def rungs_for(row: Any) -> list[str]:
    """The closed kinds this card climbs, in ladder order; the first is the primary check."""
    return _json_list(row, "rungs_json")


def triage_enabled(row: Any) -> bool:
    """Whether new cards of this card's chapter start with the know/don't-know triage."""
    return bool(row["triage_enabled"])


def hash_prefix(row: Any) -> str:
    """`h8`: the start of the card's check version, carried in every step id."""
    return row["check_version"][:HASH_PREFIX_LENGTH]


def current_events(row: Any, events: Iterable[Any]) -> list[Any]:
    """Events answered against the card as it is now (same check version).

    Every derivation from history goes through this filter, so a card whose
    answer was edited is treated as never seen.
    """
    version = row["check_version"]
    return [event for event in events if event["check_version"] == version]


def step_is_available(row: Any, kind: str) -> bool:
    """Whether the engine may currently serve a step of this kind for this card."""
    if kind == "flash":
        return True
    if kind == "triage":
        return triage_enabled(row)
    return kind in rungs_for(row)


def _shuffled(values: list[str], seed: str) -> list[str]:
    """Deterministic order from a seed, never the identity permutation.

    Alphabetical order would be a hint on numeric answers (12% before 24%
    before 36%), and the authored order puts the right option first.
    """
    order = list(range(len(values)))
    random.Random(seed).shuffle(order)
    if order == sorted(order) and len(order) > 1:
        order[0], order[1] = order[1], order[0]
    return [values[index] for index in order]


def _prompt_for(row: Any, step_id: str) -> str:
    """Rotate between the main wording and its variants, deterministically per step."""
    variants = [row["prompt"], *_json_list(row, "prompt_variants_json")]
    return variants[sum(ord(char) for char in step_id) % len(variants)]


def cloze_parts(row: Any) -> tuple[str, str] | None:
    """(prefix, suffix) around the key inside the answer, or None."""
    key = row["cloze_key"]
    if not key:
        return None
    span = find_key_span(row["answer"], key)
    if span is None:
        return None
    return row["answer"][: span[0]], row["answer"][span[1] :]


# ------------------------------------------------------------------ verdicts
def closed_step_correct(row: Any, kind: str, answer: str) -> bool:
    """The server's verdict on a closed step, from the answer text alone."""
    if kind == "choice":
        return normalize_text(answer) == normalize_text(row["option"])
    if kind == "cloze":
        key = row["cloze_key"]
        return bool(key) and normalize_text(answer) == normalize_text(key)
    if kind == "assemble":
        return normalize_text(answer) == normalize_text(row["answer"])
    raise ValueError(f"{kind} is not a closed step")


def event_correct(event: Any, row: Any) -> bool:
    """Recomputed, always. `event["correct"]` is the client's claim, not evidence."""
    return closed_step_correct(row, event["kind"], event["answer"] or "")


def is_closed(event: Any) -> bool:
    return event["kind"] in CLOSED_KINDS


def is_primary(event: Any) -> bool:
    return is_closed(event) and (event["step_id"] or "").startswith(PRIMARY_PREFIX)


def is_ladder(event: Any) -> bool:
    return (event["step_id"] or "").startswith(LADDER_PREFIX)


# ------------------------------------------------------------- step payloads
def _base_step(row: Any, kind: str, step_id: str) -> dict[str, Any]:
    if len(step_id) > STEP_ID_MAX:
        raise ValueError("step id exceeds the database limit")
    if kind not in STEP_KINDS:
        raise ValueError(f"unknown step kind: {kind}")
    return {
        "id": step_id,
        "kind": kind,
        "card_id": row["id"],
        "prompt": _prompt_for(row, step_id),
        "answer": row["answer"],
        "hint": row["hint"],
        "note": row["note"],
    }


def _with_payload(row: Any, step: dict[str, Any]) -> dict[str, Any]:
    """Add the material a closed step needs: buttons, a gap, or word tiles."""
    kind = step["kind"]
    if kind == "choice":
        step["options"] = _shuffled([row["option"], *_json_list(row, "distractors_json")], step["id"])
    elif kind == "cloze":
        parts = cloze_parts(row)
        if parts is None:
            raise ValueError(f"{row['id']} has no cloze gap")
        step["prefix"], step["suffix"] = parts
        step["options"] = _shuffled(_json_list(row, "cloze_options_json"), step["id"])
    elif kind == "assemble":
        step["tiles"] = _shuffled(row["answer"].split(), step["id"])
    return step


def triage_step(row: Any, attempt: int = 0) -> dict[str, Any]:
    return _base_step(row, "triage", f"triage:{row['id']}:{hash_prefix(row)}:{attempt}")


def flash_step(row: Any, attempt: int = 0) -> dict[str, Any]:
    return _base_step(row, "flash", f"flash:{row['id']}:{hash_prefix(row)}:{attempt}")


def primary_step(row: Any, attempt: int = 0) -> dict[str, Any]:
    """The one closed check of a card in a session: the card's first rung."""
    rungs = rungs_for(row)
    if not rungs:
        raise ValueError(f"{row['id']} has no enabled closed exercise")
    kind = rungs[0]
    step_id = f"{PRIMARY_PREFIX}{kind}:{row['id']}:{hash_prefix(row)}:{attempt}"
    return _with_payload(row, _base_step(row, kind, step_id))


def ladder_step(row: Any, kind: str, trigger_event_id: str, attempt: int) -> dict[str, Any]:
    """One rung of the ladder, identified by the missed check that opened it."""
    if kind not in CLOSED_KINDS or not trigger_event_id or attempt < 0:
        raise ValueError("invalid ladder step identity")
    step_id = f"{LADDER_PREFIX}{kind}:{row['id']}:{hash_prefix(row)}:{trigger_event_id}:{attempt}"
    step = _base_step(row, kind, step_id)
    step["repeat"] = attempt
    return _with_payload(row, step)


def _is_count(text: str) -> bool:
    """An attempt counter as the engine writes it: ASCII digits, no leading zero."""
    return text.isascii() and text.isdecimal() and (text == "0" or not text.startswith("0"))


def step_id_matches(step_id: str | None, kind: str, row: Any) -> bool:
    """Whether this id is one the engine could issue for this card right now.

    The engine never trusts the client's verdict, and this is the other half
    of the same rule: an accepted answer moves the schedule and counts towards
    readiness, so a self-issued check (a kind the card does not climb, a rung
    posted as the primary check, a triage the course has switched off) must be
    refused, not merely graded. The card id and hash prefix must match the
    card as it is now, so steps issued before an edit of its answer are stale.
    """
    if not step_id or not isinstance(step_id, str):
        return False
    parts = step_id.split(":")
    card_id, h8 = row["id"], hash_prefix(row)
    if kind in LEARNING_KINDS:
        return (
            len(parts) == 4
            and parts[0] == kind
            and parts[1] == card_id
            and parts[2] == h8
            and _is_count(parts[3])
            and step_is_available(row, kind)
        )
    if kind not in CLOSED_KINDS:
        return False
    rungs = rungs_for(row)
    if parts[0] + ":" == PRIMARY_PREFIX:
        return (
            len(parts) == 5
            and parts[1] == kind
            and bool(rungs)
            and kind == rungs[0]
            and parts[2] == card_id
            and parts[3] == h8
            and _is_count(parts[4])
        )
    if parts[0] + ":" == LADDER_PREFIX:
        # The trigger is an event id; it sits between the hash and the attempt.
        return (
            len(parts) >= 6
            and parts[1] == kind
            and kind in rungs
            and parts[2] == card_id
            and parts[3] == h8
            and bool(":".join(parts[4:-1]))
            and _is_count(parts[-1])
        )
    return False


# ----------------------------------------------------------------- the ladder
@dataclass(frozen=True)
class LadderState:
    stage: str  # closed | needs_<kind> | needs_<kind>_retry
    kind: str | None
    trigger_event_id: str | None
    attempt: int


CLOSED = LadderState("closed", None, None, 0)


def derive_card_stage(row: Any, events: list[Any]) -> LadderState:
    """Rebuild the ladder for one card from its accepted events.

    A missed primary check opens the ladder. It walks the card's rungs from
    the first: each rung has to be answered correctly before the next one
    appears. A rung gets one retry; a second miss closes the ladder rather
    than grinding, and the card comes back through its schedule instead.
    """
    events = current_events(row, events)
    triggers = [event for event in events if is_primary(event) and not event_correct(event, row)]
    if not triggers:
        return CLOSED
    trigger = triggers[-1]
    rung_events = [
        event for event in events if event["accepted_order"] > trigger["accepted_order"] and is_ladder(event)
    ]

    baseline = trigger["accepted_order"]
    for kind in rungs_for(row):
        attempts = [
            event for event in rung_events if event["kind"] == kind and event["accepted_order"] > baseline
        ]
        passed = next((event for event in attempts if event_correct(event, row)), None)
        if passed is not None:
            baseline = passed["accepted_order"]
            continue
        if len(attempts) == 0:
            return LadderState(f"needs_{kind}", kind, trigger["id"], 0)
        if len(attempts) == 1:
            return LadderState(f"needs_{kind}_retry", kind, trigger["id"], 1)
        return CLOSED
    return CLOSED


# ----------------------------------------------------- per-card session state
def group_by_card(events: Iterable[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for event in events:
        if event["card_id"]:
            grouped.setdefault(event["card_id"], []).append(event)
    return grouped


def attempts_of(row: Any, card_events: list[Any], kind: str) -> int:
    """How many steps of this kind the card has had under its current check."""
    return sum(1 for event in current_events(row, card_events) if event["kind"] == kind)


def primary_attempt(row: Any, card_events: list[Any]) -> int:
    """Attempt number of the next primary check of this card in a session.

    The engine serves a primary check only while the card has none under its
    current version, so in practice this is 0; it is still derived from
    events, so the id the server issues and the id it accepts always agree.
    """
    return sum(1 for event in current_events(row, card_events) if is_primary(event))


def triage_attempted(row: Any, card_events: list[Any]) -> bool:
    return attempts_of(row, card_events, "triage") > 0


def card_probed(row: Any, card_events: list[Any]) -> bool:
    """The card has had its one primary check in this session, pass or fail.

    This is what stops the loop: without it a miss would re-serve the same
    check immediately, and the only way forward would be to answer dishonestly.
    """
    return any(is_primary(event) for event in current_events(row, card_events))


def card_resolved(row: Any, card_events: list[Any]) -> bool:
    """The card is fully worked through and leaves the session's plan."""
    events = current_events(row, card_events)
    primary = next((event for event in events if is_primary(event)), None)
    if primary is None:
        return False
    if event_correct(primary, row):
        return True
    return derive_card_stage(row, events).stage == "closed"


def card_is_ready(row: Any, card_events: list[Any]) -> bool:
    """Readiness: the last *primary* check of this card was correct.

    Triage is a guess before seeing anything and flash is self-assessment, so
    neither counts. Nor does a ladder rung: a correct rung is scaffolding, not
    evidence that the card is known on its own. Only a fresh primary check
    returns readiness, in a lesson, a scheduled review or practice.
    """
    primaries = [event for event in current_events(row, card_events) if is_primary(event)]
    if not primaries:
        return False
    last = max(primaries, key=lambda event: event["accepted_order"])
    return event_correct(last, row)


def card_state_of(row: Any) -> str:
    return row["cs_state"] if _has(row, "cs_state") and row["cs_state"] else "new"


def category_for(state: str, stability: float | None) -> Literal["new", "learning", "review"]:
    """New (never scheduled), learning, or review (a stable, mature interval)."""
    if state == "new":
        return "new"
    if state == "review" and stability is not None and stability >= MATURE_STABILITY_DAYS:
        return "review"
    return "learning"


def card_category(row: Any) -> Literal["new", "learning", "review"]:
    """`category_for` a card row joined to the learner's schedule."""
    return category_for(card_state_of(row), row["cs_stability"] if _has(row, "cs_stability") else None)
