"""End-to-end acceptance of the learning API over the minimal course.

The fixture course has one chapter, `basics`, with two lessons: `first`
(capital-of-france: choice/cloze/assemble, powerhouse-organelle: choice) and
`second` (light-versus-sound: assemble only, primary-colors: choice/assemble).
"""

from __future__ import annotations

import json

import pytest

from app.content import Card, Program, load_program
from app.session import CLOSED_KINDS, STEP_KINDS
from tests.conftest import FIXTURE_CONTENT
from tests.helpers import (
    answer,
    api_card,
    api_db,
    api_user_id,
    complete_lesson,
    drain,
    make_due,
    post_answers,
    right_answer,
    sign_in,
    step_event,
    write_program,
    wrong_answer,
)


def start(client, lesson_id: str) -> dict:
    response = client.post(f"/api/lessons/{lesson_id}/start")
    assert response.status_code == 200, response.text
    return response.json()


def wrong(client, step: dict) -> str:
    return wrong_answer(api_card(client, step["card_id"]), step)


def right(client, step: dict) -> str:
    return right_answer(api_card(client, step["card_id"]), step)


def edited_program(mutate) -> Program:
    """The fixture program with `mutate(program)` applied, for a direct content upsert."""
    program = load_program(FIXTURE_CONTENT).model_copy(deep=True)
    mutate(program)
    return program


def card_of_program(program, card_id: str) -> Card:
    return next(
        card
        for chapter in program.chapters
        for lesson in chapter.lessons
        for card in lesson.cards
        if card.id == card_id
    )


def lesson_of_program(program, lesson_id: str):
    return next(
        lesson for chapter in program.chapters for lesson in chapter.lessons if lesson.id == lesson_id
    )


def course_client(make_client, tmp_path, **course):
    """A signed-in client on a course written by `write_program` (three-card default layout)."""
    return sign_in(make_client(content_dir=write_program(tmp_path, **course)))


# ------------------------------------------------------------------ opening
def test_a_fresh_account_can_open_a_session_immediately(signed_in):
    chapters = signed_in.get("/api/chapters").json()
    assert chapters["cards_total"] == 4
    lesson_id = chapters["chapters"][0]["lessons"][0]["id"]
    started = start(signed_in, lesson_id)
    assert started["steps"]
    assert started["mode"] == "lesson"
    assert started["session_id"] == started["lesson_id"] == lesson_id
    assert started["title"] == "First lesson"
    assert (started["total_cards"], started["resolved_cards"]) == (2, 0)


def test_every_lesson_is_open_from_the_start(signed_in):
    chapters = signed_in.get("/api/chapters").json()
    lessons = [lesson for chapter in chapters["chapters"] for lesson in chapter["lessons"]]
    assert [lesson["id"] for lesson in lessons] == ["first", "second"]
    assert all(lesson["status"] == "available" for lesson in lessons)


def test_an_unfinished_lesson_does_not_block_another_one(signed_in):
    start(signed_in, "second")
    start(signed_in, "first")


def test_unknown_lesson_is_404(signed_in):
    assert signed_in.post("/api/lessons/nope/start").status_code == 404
    assert signed_in.post("/api/lessons/nope/practice").status_code == 404


# --------------------------------------------------------------- step kinds
def test_only_known_kinds_are_ever_served(signed_in):
    seen = set()
    for lesson_id in ("first", "second"):
        seen.update(step["kind"] for step in start(signed_in, lesson_id)["steps"])
    assert seen <= STEP_KINDS
    assert {"triage", "choice", "assemble"} <= seen


@pytest.mark.parametrize("kind", ["type", "match", "", None])
def test_an_unknown_kind_is_rejected_on_submission(signed_in, kind):
    payload = start(signed_in, "first")
    step = payload["steps"][0]
    event = {**step_event(payload["session_id"], step, "whatever"), "kind": kind, "step_id": f"x:{kind}"}
    body = post_answers(signed_in, [event])
    result = body["results"][0]
    assert result["accepted"] is False
    assert result["rejected"] == "invalid"
    assert "unknown step kind" in result["detail"]
    assert api_db(signed_in).scalar("SELECT count(*) FROM events") == 0


def test_triage_comes_first_and_the_closed_check_follows_in_the_same_session(signed_in):
    kinds = [step["kind"] for step in start(signed_in, "first")["steps"]]
    assert kinds[0] == "triage"
    assert "choice" in kinds, "deferring the closed check leaves the board at zero on day one"


def test_choice_offers_four_options_in_a_shuffled_order(signed_in):
    choices = [step for step in start(signed_in, "first")["steps"] if step["kind"] == "choice"]
    assert len(choices) == 2
    for step in choices:
        card = api_card(signed_in, step["card_id"])
        authored = [card["option"], *json.loads(card["distractors_json"])]
        assert sorted(step["options"]) == sorted(authored)
        assert len(set(step["options"])) == 4
        assert step["options"] != authored, "the authored order puts the right option first"


def test_the_prompt_alternates_between_wordings(make_client, tmp_path):
    wordings = ["Which planet is the largest?", "Name the biggest planet.", "Which planet has the most mass?"]
    client = course_client(make_client, tmp_path, cards={"largest-planet": {"prompt_variants": wordings[1:]}})
    seen = set()
    payload = start(client, "first")
    for _ in range(20):
        for step in payload["steps"]:
            if step["card_id"] == "largest-planet":
                assert step["prompt"] in wordings
                seen.add(step["prompt"])
        if not payload["steps"]:
            break
        step = payload["steps"][0]
        miss = step["card_id"] == "largest-planet" and step["id"].startswith("p1:")
        payload = answer(client, "first", step, wrong(client, step) if miss else right(client, step))[
            "session"
        ]
    assert len(seen) > 1


def test_closed_exercises_appear_only_where_the_data_allows(signed_in):
    primaries = {
        step["card_id"]: step["kind"]
        for lesson_id in ("first", "second")
        for step in start(signed_in, lesson_id)["steps"]
        if step["id"].startswith("p1:")
    }
    assert primaries == {
        "capital-of-france": "choice",
        "powerhouse-organelle": "choice",
        "light-versus-sound": "assemble",  # no distractors, so no choice
        "primary-colors": "choice",
    }


def test_an_enumeration_is_never_scored_by_one_element(signed_in):
    card = api_card(signed_in, "primary-colors")
    assert card["key_mode"] == "all_of"
    assert card["cloze_key"] is None
    assert "cloze" not in card["rungs_json"]


def test_closed_kinds_are_exactly_the_server_graded_ones():
    assert {"choice", "cloze", "assemble"} == CLOSED_KINDS


def test_steps_carry_prompt_answer_hint_and_note(signed_in):
    steps = start(signed_in, "first")["steps"] + start(signed_in, "second")["steps"]
    for step in steps:
        assert step["prompt"] and step["answer"]
        assert "hint" in step and "note" in step
    hints = {step["card_id"]: step["hint"] for step in steps}
    notes = {step["card_id"]: step["note"] for step in steps}
    assert hints["powerhouse-organelle"] == "It is often called the powerhouse of the cell."
    assert notes["primary-colors"] == "Mixing any two primaries gives a secondary color."


# ---------------------------------------------------------------- the ladder
def test_a_miss_returns_the_card_after_three_other_exercises(signed_in):
    payload = start(signed_in, "first")
    first_choice = next(step for step in payload["steps"] if step["kind"] == "choice")
    result = answer(signed_in, "first", first_choice, wrong(signed_in, first_choice), correct=True)
    assert result["correct"] is False, "the server grades, not the client"
    steps = result["session"]["steps"]
    positions = [
        index
        for index, step in enumerate(steps)
        if step["card_id"] == first_choice["card_id"] and step["id"].startswith("v2:")
    ]
    assert positions, "the missed card must be scheduled to come back"
    assert positions[0] >= 3, f"came back after {positions[0]} exercises, expected at least 3"


def test_a_one_card_lesson_borrows_the_gap_from_its_chapter(make_client, tmp_path):
    client = course_client(make_client, tmp_path)
    complete_lesson(client, "first")  # gives the chapter worked cards to borrow from

    payload = start(client, "second")
    choice = next(step for step in payload["steps"] if step["kind"] == "choice")
    steps = answer(client, "second", choice, wrong(client, choice))["session"]["steps"]
    ladder = [index for index, step in enumerate(steps) if step["id"].startswith("v2:")]
    assert ladder, "the retry must be reachable, not silently dropped"
    assert ladder[0] >= 3
    assert any(step["card_id"] != choice["card_id"] for step in steps[: ladder[0]]), (
        "the spacing has to be filled with real work from the chapter"
    )


def test_a_ladder_rung_is_never_dropped_when_the_lesson_cannot_space_it(signed_in):
    payload = start(signed_in, "first")
    missed_card = None
    for _ in range(40):
        steps = payload["steps"]
        if not steps:
            break
        step = steps[0]
        if step["kind"] == "choice" and missed_card is None:
            missed_card = step["card_id"]
            payload = answer(signed_in, "first", step, wrong(signed_in, step))["session"]
            continue
        payload = answer(signed_in, "first", step, right(signed_in, step))["session"]
    else:
        raise AssertionError("lesson did not terminate")

    assert missed_card, "the lesson must have offered a closed check"
    ladder = api_db(signed_in).fetch_cards(
        "SELECT step_id FROM events WHERE card_id=? AND step_id LIKE 'v2:%'", (missed_card,)
    )
    assert ladder, "the missed card never came back"
    assert api_db(signed_in).user_lesson_state(api_user_id(signed_in), "first")["status"] == "completed"


def test_full_lesson_with_miss_and_ladder_via_api(signed_in):
    payload = start(signed_in, "first")
    served: list[dict] = []
    for _ in range(60):
        if not payload["steps"]:
            break
        step = payload["steps"][0]
        served.append(step)
        miss = step["id"].startswith("p1:") and step["card_id"] == "capital-of-france"
        result = answer(signed_in, "first", step, wrong(signed_in, step) if miss else right(signed_in, step))
        assert result["accepted"] and not result["duplicate"] and result["rejected"] is None
        if step["kind"] in CLOSED_KINDS:
            assert result["correct"] is (not miss)
        else:
            assert result["correct"] is None
        assert result["state_conflict"] is False
        payload = result["session"]
        assert payload["session_id"] == "first"
    else:
        raise AssertionError("lesson did not terminate")

    capital = [step for step in served if step["card_id"] == "capital-of-france"]
    assert [step["id"].split(":")[0] for step in capital[:2]] == ["triage", "p1"]
    assert sum(1 for step in capital if step["kind"] == "flash") == 1, "the missed card is read again"
    ladder = [step["kind"] for step in capital if step["id"].startswith("v2:")]
    assert ladder == ["choice", "cloze", "assemble"], "the ladder climbs every rung from the first"
    assert (payload["total_cards"], payload["resolved_cards"]) == (2, 2)
    chapters = signed_in.get("/api/chapters").json()
    assert chapters["chapters"][0]["lessons"][0]["status"] == "completed"
    # Only the correct primary check counts; the ladder never makes a card ready.
    assert chapters["cards_ready"] == 1


def test_a_forged_verdict_moves_neither_the_schedule_nor_the_ladder(signed_in):
    payload = start(signed_in, "first")
    choice = next(step for step in payload["steps"] if step["kind"] == "choice")
    result = answer(signed_in, "first", choice, wrong(signed_in, choice), correct=True)
    assert result["correct"] is False
    stored = api_db(signed_in).fetch_cards(
        "SELECT rating, correct FROM events WHERE step_id=?", (choice["id"],)
    )[0]
    assert stored["rating"] == "again"
    assert stored["correct"] == 0, "the stored verdict is the server's, not the client's"


@pytest.mark.parametrize("lesson_id", ["first", "second"])
def test_a_lesson_finishes_after_a_miss(make_client, tmp_path, lesson_id):
    """The chapter filler must never re-serve a step the learner already spent."""
    client = course_client(make_client, tmp_path)
    for other in ("first", "second"):
        if other != lesson_id:
            complete_lesson(client, other)

    payload = start(client, lesson_id)
    missed = False
    for _ in range(60):
        if not payload["steps"]:
            break
        step = payload["steps"][0]
        if step["kind"] == "choice" and not missed:
            missed = True
            result = answer(client, lesson_id, step, wrong(client, step))
        else:
            result = answer(client, lesson_id, step, right(client, step))
        assert not result["duplicate"], f"replayed step {step['id']}: the session is stuck"
        payload = result["session"]
    else:
        raise AssertionError("lesson did not finish")
    assert missed
    assert api_db(client).user_lesson_state(api_user_id(client), lesson_id)["status"] == "completed"


# ---------------------------------------------------------------- readiness
def test_the_board_is_not_zero_after_the_very_first_session(signed_in):
    before = signed_in.get("/api/chapters").json()
    assert before["cards_ready"] == 0
    complete_lesson(signed_in, "first")
    after = signed_in.get("/api/chapters").json()
    assert after["cards_ready"] == 2, "two cards passed a closed check"
    assert after["cards_total"] == 4


def test_self_assessment_alone_does_not_count_as_ready(signed_in):
    payload = start(signed_in, "first")
    triage = payload["steps"][0]
    assert triage["kind"] == "triage"
    answer(signed_in, "first", triage, "know")
    assert signed_in.get("/api/chapters").json()["cards_ready"] == 0


def test_chapters_next_lesson(signed_in):
    view = signed_in.get("/api/chapters").json()
    assert view["next_lesson"] == {
        "id": "first",
        "title": "First lesson",
        "chapter_id": "basics",
        "action": "start",
    }

    payload = start(signed_in, "second")
    answer(signed_in, "second", payload["steps"][0], right(signed_in, payload["steps"][0]))
    view = signed_in.get("/api/chapters").json()
    assert view["next_lesson"]["id"] == "second"
    assert view["next_lesson"]["action"] == "continue"

    drain(signed_in, signed_in.get("/api/session", params={"session_id": "second"}).json())
    view = signed_in.get("/api/chapters").json()
    assert view["next_lesson"]["id"] == "first"
    complete_lesson(signed_in, "first")
    view = signed_in.get("/api/chapters").json()
    assert view["next_lesson"] is None
    assert view["practice_available"] is True
    assert view["practice_card_count"] == 4


def test_settings_show_hint_round_trip(signed_in):
    assert signed_in.get("/api/settings").json() == {"show_hint_by_default": True}
    updated = signed_in.post("/api/settings", json={"show_hint_by_default": False})
    assert updated.status_code == 200
    assert updated.json() == {"show_hint_by_default": False}
    assert signed_in.get("/api/settings").json() == {"show_hint_by_default": False}
    assert signed_in.post("/api/settings", json={}).json() == {"show_hint_by_default": False}
    assert signed_in.post("/api/settings", json={"show_hint_by_default": "yes"}).status_code == 422
    assert signed_in.post("/api/settings", json={"unknown": True}).status_code == 422


def test_progress_matches_events(signed_in):
    empty = signed_in.get("/api/progress").json()
    assert empty["cards_ready"] == 0
    assert empty["checks_30d"] == 0
    assert empty["lessons_completed"] == 0

    payload = start(signed_in, "first")
    for _ in range(60):
        if not payload["steps"]:
            break
        step = payload["steps"][0]
        miss = step["id"].startswith("p1:") and step["card_id"] == "powerhouse-organelle"
        payload = answer(
            signed_in, "first", step, wrong(signed_in, step) if miss else right(signed_in, step)
        )["session"]
    events = api_db(signed_in).fetch_cards("SELECT * FROM events")
    primaries = [event for event in events if (event["step_id"] or "").startswith("p1:")]
    progress = signed_in.get("/api/progress").json()
    assert progress["checks_30d"] == len(primaries) == 2
    assert progress["retention_30d"] == 50
    assert progress["cards_ready"] == 1
    assert progress["cards_total"] == 4
    assert progress["lessons_total"] == 2
    assert progress["lessons_completed"] == 1
    assert progress["session_minutes"] == round(sum(event["elapsed_ms"] for event in events) / 60000)
    assert [card["id"] for card in progress["problem_cards"]] == ["powerhouse-organelle"]
    assert progress["problem_cards"][0]["again_count"] == sum(
        1 for event in events if event["card_id"] == "powerhouse-organelle" and event["rating"] == "again"
    )
    assert sum(day["amount"] for day in progress["forecast_7d"]) == 2


# ------------------------------------------------------------------ content
def test_editing_the_answer_resets_that_card_schedule(signed_in):
    complete_lesson(signed_in, "first")
    database = api_db(signed_in)
    assert database.fetch_cards(
        "SELECT 1 FROM card_state WHERE card_id='powerhouse-organelle' AND state != 'new'"
    )

    def edit(program):
        card = card_of_program(program, "powerhouse-organelle")
        card.answer = "The mitochondria"
        card.option = "Mitochondria"

    database.upsert_program(edited_program(edit))
    assert not database.fetch_cards(
        "SELECT 1 FROM card_state WHERE card_id='powerhouse-organelle' AND state != 'new'"
    ), "a schedule earned against a different answer must not survive"
    lesson = signed_in.get("/api/chapters").json()["chapters"][0]["lessons"][0]
    assert lesson["has_open_work"] is True


def test_a_card_added_to_a_finished_lesson_reopens_it(signed_in):
    complete_lesson(signed_in, "first")
    assert api_db(signed_in).user_lesson_state(api_user_id(signed_in), "first")["status"] == "completed"

    def add(program):
        lesson_of_program(program, "first").cards.append(
            Card(
                id="largest-ocean",
                prompt="Which ocean is the largest?",
                answer="The Pacific",
                option="Pacific",
                distractors=["Atlantic", "Indian", "Arctic"],
            )
        )

    api_db(signed_in).upsert_program(edited_program(add))
    payload = start(signed_in, "first")
    assert any(step["card_id"] == "largest-ocean" for step in payload["steps"])


def test_a_retired_lesson_cannot_be_started(signed_in):
    def drop(program):
        program.chapters[0].lessons = [
            lesson for lesson in program.chapters[0].lessons if lesson.id != "second"
        ]

    start(signed_in, "second")
    api_db(signed_in).upsert_program(edited_program(drop))
    assert signed_in.post("/api/lessons/second/start").status_code == 404
    assert signed_in.post("/api/lessons/second/practice").status_code == 404
    assert signed_in.get("/api/session", params={"session_id": "second"}).status_code == 404


def test_a_lesson_whose_remaining_cards_were_retired_completes(signed_in):
    payload = start(signed_in, "first")
    while payload["steps"] and any(step["card_id"] == "capital-of-france" for step in payload["steps"]):
        step = next(step for step in payload["steps"] if step["card_id"] == "capital-of-france")
        payload = answer(signed_in, "first", step, right(signed_in, step))["session"]

    def retire(program):
        lesson = lesson_of_program(program, "first")
        lesson.cards = [card for card in lesson.cards if card.id != "powerhouse-organelle"]

    api_db(signed_in).upsert_program(edited_program(retire))
    view = signed_in.get("/api/session", params={"session_id": "first"}).json()
    assert view["steps"] == []
    assert api_db(signed_in).user_lesson_state(api_user_id(signed_in), "first")["status"] == "completed"
    assert signed_in.get("/api/chapters").json()["next_lesson"]["id"] == "second"


# -------------------------------------------------------------- idempotency
def test_the_same_step_answered_twice_counts_once(signed_in):
    payload = start(signed_in, "first")
    step = payload["steps"][0]
    first = answer(signed_in, "first", step, right(signed_in, step))
    assert first["duplicate"] is False
    replay = answer(signed_in, "first", step, right(signed_in, step))
    assert replay["accepted"] is True
    assert replay["duplicate"] is True
    assert api_db(signed_in).scalar("SELECT count(*) FROM events WHERE step_id=?", (step["id"],)) == 1


def test_answers_idempotent(signed_in):
    payload = start(signed_in, "first")
    steps = payload["steps"][:2]
    batch = [
        step_event("first", step, right(signed_in, step), event_id=f"event-{index}")
        for index, step in enumerate(steps)
    ]
    first = post_answers(signed_in, batch)
    assert [(r["accepted"], r["duplicate"]) for r in first["results"]] == [(True, False), (True, False)]
    again = post_answers(signed_in, batch)
    assert [(r["accepted"], r["duplicate"]) for r in again["results"]] == [(True, True), (True, True)]
    assert [r["step_id"] for r in again["results"]] == [step["id"] for step in steps]
    assert again["session"] == first["session"]
    assert api_db(signed_in).scalar("SELECT count(*) FROM events") == 2
    # The same event id with another step is still the same answer.
    reused = post_answers(
        signed_in, [{**batch[0], "step_id": steps[1]["id"], "card_id": steps[1]["card_id"]}]
    )
    assert reused["results"][0]["duplicate"] is True
    assert api_db(signed_in).scalar("SELECT count(*) FROM events") == 2


def test_results_shape(signed_in):
    payload = start(signed_in, "first")
    body = post_answers(signed_in, [step_event("first", payload["steps"][0], "know")])
    assert set(body) == {"results", "session", "state_conflict"}
    assert body["results"] == [
        {
            "step_id": payload["steps"][0]["id"],
            "accepted": True,
            "duplicate": False,
            "correct": None,
            "rejected": None,
            "detail": None,
        }
    ]
    assert set(body["session"]) == {
        "session_id",
        "lesson_id",
        "title",
        "mode",
        "steps",
        "total_cards",
        "resolved_cards",
    }


def test_batch_must_be_a_non_empty_list(signed_in):
    assert signed_in.post("/api/answers", json={"events": []}).status_code == 422
    assert signed_in.post("/api/answers", json=[]).status_code == 422
    assert signed_in.post("/api/answers", json={"events": [{}] * 101}).status_code == 422
    body = post_answers(signed_in, ["not an event"])
    assert body["results"][0]["rejected"] == "invalid"
    assert body["session"] is None


# ---------------------------------------------------------------- staleness
def test_a_self_issued_cloze_on_an_ineligible_card_is_refused(signed_in):
    """Grading honestly is not enough: the check itself must have been offered."""
    payload = start(signed_in, "first")
    h8 = api_card(signed_in, "powerhouse-organelle")["check_version"][:8]
    body = post_answers(
        signed_in,
        [
            {
                "session_id": payload["session_id"],
                "step_id": f"v2:cloze:powerhouse-organelle:{h8}:e1:0",
                "card_id": "powerhouse-organelle",
                "kind": "cloze",
                "answer": "Mitochondria",
                "elapsed_ms": 100,
            }
        ],
    )
    assert body["results"][0]["rejected"] == "stale"
    assert body["results"][0]["accepted"] is False


def test_a_self_issued_assemble_cannot_mark_a_card_ready(signed_in):
    payload = start(signed_in, "first")
    card = api_card(signed_in, "powerhouse-organelle")
    body = post_answers(
        signed_in,
        [
            {
                "session_id": payload["session_id"],
                "step_id": f"p1:assemble:powerhouse-organelle:{card['check_version'][:8]}:0",
                "card_id": "powerhouse-organelle",
                "kind": "assemble",
                "answer": card["answer"],
                "elapsed_ms": 100,
            }
        ],
    )
    assert body["results"][0]["rejected"] == "stale"
    assert signed_in.get("/api/chapters").json()["cards_ready"] == 0


def test_a_step_id_from_another_card_is_refused(signed_in):
    payload = start(signed_in, "first")
    choice = next(
        step
        for step in payload["steps"]
        if step["card_id"] == "capital-of-france" and step["kind"] == "choice"
    )
    event = {**step_event("first", choice, "Mitochondria"), "card_id": "powerhouse-organelle"}
    body = post_answers(signed_in, [event])
    assert body["results"][0]["rejected"] == "stale"
    assert body["results"][0]["detail"]


def test_one_unusable_buffered_event_does_not_block_the_rest(signed_in):
    payload = start(signed_in, "first")
    step = payload["steps"][0]
    good = step_event("first", step, right(signed_in, step))
    doomed = {**good, "card_id": "does-not-exist", "step_id": "triage:does-not-exist:00000000:0"}
    body = post_answers(signed_in, [doomed, good])
    assert body["results"][0]["accepted"] is False
    assert body["results"][0]["rejected"] == "invalid"
    assert body["results"][1]["accepted"] is True
    assert api_db(signed_in).scalar("SELECT count(*) FROM events WHERE step_id=?", (step["id"],)) == 1


def test_stale_step_rejected_in_batch_with_refreshed_session(signed_in):
    payload = start(signed_in, "first")
    triage = next(step for step in payload["steps"] if step["card_id"] == "powerhouse-organelle")
    choice = next(step for step in payload["steps"] if step["kind"] == "choice")

    def edit(program):
        card_of_program(program, "powerhouse-organelle").answer = "The mitochondria"

    api_db(signed_in).upsert_program(edited_program(edit))
    body = post_answers(
        signed_in,
        [step_event("first", triage, "know"), step_event("first", choice, right(signed_in, choice))],
    )
    stale, fine = body["results"]
    assert (stale["accepted"], stale["rejected"]) == (False, "stale")
    assert stale["detail"] == "this step is no longer current"
    assert (fine["accepted"], fine["rejected"], fine["correct"]) == (True, None, True)
    refreshed = body["session"]["steps"]
    assert triage["id"] not in {step["id"] for step in refreshed}
    assert any(
        step["card_id"] == "powerhouse-organelle" and step["kind"] == "triage" for step in refreshed
    ), "the edited card is served again under its new check"
    assert body["state_conflict"] is False


def test_stale_step_after_config_change_is_rejected_others_accepted(make_client, tmp_path):
    client = sign_in(make_client(content_dir=write_program(tmp_path)))
    payload = start(client, "first")
    triages = [step for step in payload["steps"] if step["kind"] == "triage"]
    primary = next(step for step in payload["steps"] if step["id"].startswith("p1:choice:"))

    # Restart on the same database with triage switched off and choice replaced by cloze.
    restarted = make_client(content_dir=write_program(tmp_path, exercises={"triage": False, "choice": False}))
    restarted.cookies.update(client.cookies)
    fresh = restarted.get("/api/session", params={"session_id": "first"}).json()
    cloze = next(step for step in fresh["steps"] if step["card_id"] == "largest-planet")
    assert cloze["kind"] == "cloze"

    body = post_answers(
        restarted,
        [
            step_event("first", triages[0], "know"),
            step_event("first", primary, right(restarted, primary)),
            step_event("first", cloze, right(restarted, cloze)),
        ],
    )
    assert [result["rejected"] for result in body["results"]] == ["stale", "stale", None]
    assert body["results"][2]["accepted"] is True and body["results"][2]["correct"] is True
    assert api_db(restarted).scalar("SELECT count(*) FROM events") == 1
    assert all(step["kind"] != "triage" for step in body["session"]["steps"])


def test_state_conflict_flags_a_step_that_comes_back_unchanged(signed_in):
    payload = start(signed_in, "first")
    triage = payload["steps"][0]
    body = post_answers(signed_in, [step_event("first", triage, "maybe")])
    assert body["results"][0]["rejected"] == "invalid"
    assert body["state_conflict"] is True


# --------------------------------------------------------------- review
def test_a_finished_lesson_feeds_the_review_plan(signed_in):
    complete_lesson(signed_in, "first")
    make_due(signed_in)
    view = signed_in.get("/api/chapters").json()
    assert view["review_due"] == 2
    assert view["review_sessions_remaining"] == 1
    started = signed_in.post("/api/review/start")
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["mode"] == "scheduled_review"
    assert body["title"] == "Review"
    assert body["lesson_id"] is None
    assert {step["kind"] for step in body["steps"]} == {"choice"}
    assert signed_in.get("/api/session").json()["session_id"] == body["session_id"]
    done = drain(signed_in, body)
    assert done["resolved_cards"] == done["total_cards"] == 2
    assert signed_in.get("/api/chapters").json()["review_due"] == 0
    assert signed_in.post("/api/review/start").status_code == 404


def test_review_without_anything_due_says_so(signed_in):
    response = signed_in.post("/api/review/start")
    assert response.status_code == 404
    assert response.json() == {"detail": "no reviews due"}


def test_session_without_id_resumes_the_lesson_in_progress(signed_in):
    empty = signed_in.get("/api/session").json()
    assert empty["steps"] == []
    assert empty["mode"] == "scheduled_review"
    start(signed_in, "second")
    assert signed_in.get("/api/session").json()["session_id"] == "second"


# ---------------------------------------------------------------- health
def test_health_reports_the_seeded_course(client):
    body = client.get("/api/health").json()
    assert (body["chapters"], body["lessons"], body["cards"]) == (1, 2, 4)
    assert body["program_version"] == load_program(FIXTURE_CONTENT).program_version


# ------------------------------------------------------------ hostile input
@pytest.mark.parametrize(
    "poison",
    [
        {"ts": "0001-01-01T00:00:00+05:00"},
        {"timing_version": 2**70},
        {"timing_version": 5000},
        {"answer": "\ud800"},
        {"session_id": "first\udfff"},
    ],
)
def test_one_poisoned_event_does_not_fail_the_batch(signed_in, poison):
    payload = start(signed_in, "first")
    triage, other = payload["steps"][0], payload["steps"][1]
    events = [{**step_event("first", triage, "know"), **poison}, step_event("first", other, "know")]
    # Sent as ASCII JSON: a lone surrogate is legal there as a `\ud800` escape.
    response = signed_in.post(
        "/api/answers", content=json.dumps({"events": events}), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [result["rejected"] for result in body["results"]] == ["invalid", None]
    assert body["results"][1]["accepted"] is True
    assert api_db(signed_in).scalar("SELECT count(*) FROM events") == 1


def test_a_future_timestamp_is_stored_as_now(signed_in):
    payload = start(signed_in, "first")
    event = {**step_event("first", payload["steps"][0], "know"), "ts": "2999-01-01T00:00:00Z"}
    assert post_answers(signed_in, [event])["results"][0]["accepted"] is True
    stored = api_db(signed_in).scalar("SELECT ts FROM events")
    assert stored < "2999"


def test_a_hostile_kind_is_neither_logged_nor_echoed_raw(signed_in, caplog):
    payload = start(signed_in, "first")
    kind = "choice\nFAKE LOG LINE " + "x" * 500
    event = {**step_event("first", payload["steps"][0], "know"), "kind": kind, "step_id": "a\nb"}
    with caplog.at_level("WARNING"):
        body = post_answers(signed_in, [event])
    detail = body["results"][0]["detail"]
    assert body["results"][0]["rejected"] == "invalid"
    assert "\n" not in detail and "FAKE LOG LINE" in detail
    assert len(detail) <= 100
    messages = [record.getMessage() for record in caplog.records]
    assert any("FAKE LOG LINE" in message for message in messages)
    assert all("\n" not in message and len(message) < 300 for message in messages)


def test_two_learners_may_use_the_same_event_id(client):
    for username in ("alice", "bob"):
        sign_in(client, username)
        payload = start(client, "first")
        result = answer(client, "first", payload["steps"][0], "know", event_id="buffer-1")
        assert (result["accepted"], result["duplicate"]) == (True, False), username
        replay = answer(client, "first", payload["steps"][0], "know", event_id="buffer-1")
        assert replay["duplicate"] is True
    assert api_db(client).scalar("SELECT count(*) FROM events WHERE id='buffer-1'") == 2
