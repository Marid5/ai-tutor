"""Voluntary practice over HTTP: replaying a completed lesson, and mixed practice.

Practice never stretches an interval: a correct answer leaves the schedule
alone, a missed primary check applies Again and joins today's review plan.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from app import curriculum
from app.content import Card, load_program
from app.session import PRACTICE_CARDS_PER_SESSION, mixed_practice_pool
from app.steps import step_id_matches
from tests.conftest import FIXTURE_CONTENT
from tests.helpers import (
    answer,
    api_card,
    api_db,
    api_user_id,
    complete_lesson,
    drain,
    make_due,
    right_answer,
    sign_in,
    wrong_answer,
)


def practice(client, lesson_id: str, expect: int = 200) -> dict:
    response = client.post(f"/api/lessons/{lesson_id}/practice")
    assert response.status_code == expect, response.text
    return response.json()


def choice_of(payload: dict, card_id: str | None = None) -> dict:
    return next(
        step
        for step in payload["steps"]
        if step["kind"] == "choice" and (card_id is None or step["card_id"] == card_id)
    )


def wrong(client, step: dict) -> str:
    return wrong_answer(api_card(client, step["card_id"]), step)


def right(client, step: dict) -> str:
    return right_answer(api_card(client, step["card_id"]), step)


def ready_card_ids(client) -> set[str]:
    return curriculum._ready_cards(api_db(client), api_user_id(client))


def state_of(client, card_id: str):
    return api_db(client).get_state(api_user_id(client), card_id)


def upsert_edited(client, mutate) -> None:
    program = load_program(FIXTURE_CONTENT).model_copy(deep=True)
    mutate(program)
    api_db(client).upsert_program(program)


LESSON_COUNT = 5
CARDS_PER_LESSON = 3


def numbers_course(tmp_path: Path) -> Path:
    """A one-chapter course big enough to fill a mixed-practice session (15 cards)."""
    content = tmp_path / "numbers-course"
    (content / "chapters").mkdir(parents=True)
    lessons = []
    for lesson in range(1, LESSON_COUNT + 1):
        cards = []
        for position in range(1, CARDS_PER_LESSON + 1):
            n = (lesson - 1) * CARDS_PER_LESSON + position
            cards.append(
                {
                    "id": f"item-{n}",
                    "prompt": f"Which value belongs to item {n}?",
                    "answer": f"Item {n} holds value {n}",
                    "option": f"value {n}",
                    "distractors": [f"value {n + 100}", f"value {n + 200}", f"value {n + 300}"],
                }
            )
        lessons.append({"id": f"lesson-{lesson}", "title": f"Lesson {lesson}", "cards": cards})
    program = {"schema_version": 1, "title": "Numbers", "language": "en", "chapters": ["numbers"]}
    chapter = {"id": "numbers", "title": "Numbers", "lessons": lessons}
    (content / "program.yaml").write_text(yaml.safe_dump(program), encoding="utf-8")
    (content / "chapters" / "numbers.yaml").write_text(yaml.safe_dump(chapter), encoding="utf-8")
    return content


def numbers_client(make_client, tmp_path):
    return sign_in(make_client(content_dir=numbers_course(tmp_path)))


# ------------------------------------------------------------- lesson practice
def test_practicing_an_unfinished_lesson_is_refused(signed_in):
    response = signed_in.post("/api/lessons/first/practice")
    assert response.status_code == 409
    assert response.json() == {"detail": "lesson not completed"}


def test_practicing_a_completed_lesson_replays_its_cards(signed_in):
    complete_lesson(signed_in, "first")
    body = practice(signed_in, "first")
    assert body["mode"] == "lesson_practice"
    assert body["lesson_id"] == "first"
    assert body["session_id"].startswith("practice-")
    assert body["steps"]
    assert all(step["kind"] == "choice" for step in body["steps"]), "no triage in a replay"
    assert (body["total_cards"], body["resolved_cards"]) == (2, 0)


def test_a_completed_lesson_with_new_content_is_not_a_replay(signed_in):
    complete_lesson(signed_in, "first")

    def add(program):
        program.chapters[0].lessons[0].cards.append(
            Card(
                id="new-card-for-practice",
                prompt="Which gas do plants take in?",
                answer="Carbon dioxide",
                distractors=["Oxygen", "Nitrogen", "Helium"],
            )
        )

    upsert_edited(signed_in, add)
    result = practice(signed_in, "first")
    assert result["mode"] == "lesson"
    chapters = signed_in.get("/api/chapters").json()
    assert chapters["chapters"][0]["lessons"][0]["has_open_work"] is True


def test_a_lesson_with_every_card_retired_has_nothing_to_practice(signed_in):
    complete_lesson(signed_in, "first")
    with api_db(signed_in)._transaction() as conn:
        conn.execute("UPDATE cards SET retired=1 WHERE lesson_id='first'")
    response = signed_in.post("/api/lessons/first/practice")
    assert response.status_code == 404
    assert response.json() == {"detail": "nothing to practice"}


def test_lesson_practice_does_not_touch_lesson_status_or_accuracy(signed_in):
    complete_lesson(signed_in, "first")
    before = api_db(signed_in).user_lesson_state(api_user_id(signed_in), "first")
    drain(signed_in, practice(signed_in, "first"))
    after = api_db(signed_in).user_lesson_state(api_user_id(signed_in), "first")
    assert after["status"] == "completed"
    assert after["completed_at"] == before["completed_at"]
    assert after["accuracy"] == before["accuracy"]


# ------------------------------------------------------------ session lifecycle
def test_restarting_the_same_practice_the_same_day_resumes_it(signed_in):
    complete_lesson(signed_in, "first")
    assert practice(signed_in, "first")["session_id"] == practice(signed_in, "first")["session_id"]


def test_starting_a_different_practice_abandons_the_open_one(signed_in):
    complete_lesson(signed_in, "first")
    complete_lesson(signed_in, "second")
    first = practice(signed_in, "first")
    second = practice(signed_in, "second")
    assert first["session_id"] != second["session_id"]
    abandoned = api_db(signed_in).study_session(api_user_id(signed_in), first["session_id"])
    assert abandoned["status"] == "abandoned"


def test_a_double_click_start_creates_only_one_session(signed_in):
    complete_lesson(signed_in, "first")
    first = signed_in.post("/api/lessons/first/practice")
    second = signed_in.post("/api/lessons/first/practice")
    assert first.status_code == second.status_code == 200
    assert first.json()["session_id"] == second.json()["session_id"]
    count = api_db(signed_in).scalar(
        "SELECT count(*) FROM study_sessions WHERE mode='lesson_practice' AND status='in_progress'"
    )
    assert count == 1


def test_a_practice_session_from_a_previous_day_is_abandoned_on_restart(signed_in):
    complete_lesson(signed_in, "first")
    payload = practice(signed_in, "first")
    with api_db(signed_in)._transaction() as conn:
        conn.execute("UPDATE study_sessions SET day_key='2020-01-01' WHERE id=?", (payload["session_id"],))
    restarted = practice(signed_in, "first")
    assert restarted["session_id"] != payload["session_id"]
    old = api_db(signed_in).study_session(api_user_id(signed_in), payload["session_id"])
    assert old["status"] == "abandoned"


def test_an_abandoned_practice_does_not_hijack_scheduled_review(signed_in):
    complete_lesson(signed_in, "first")
    make_due(signed_in)
    complete_lesson(signed_in, "second")
    practice(signed_in, "first")  # left in progress, never finished
    practice(signed_in, "second")  # abandons the one above

    started = signed_in.post("/api/review/start")
    assert started.status_code == 200, started.text
    assert started.json()["mode"] == "scheduled_review"


def test_get_session_by_id_covers_lesson_practice_in_progress_abandoned_and_unknown(signed_in):
    complete_lesson(signed_in, "first")
    complete_lesson(signed_in, "second")
    lesson_view = signed_in.get("/api/session", params={"session_id": "first"})
    assert lesson_view.status_code == 200
    assert lesson_view.json()["mode"] == "lesson"

    first = practice(signed_in, "first")
    in_progress_view = signed_in.get("/api/session", params={"session_id": first["session_id"]})
    assert in_progress_view.status_code == 200
    assert in_progress_view.json()["mode"] == "lesson_practice"

    practice(signed_in, "second")  # abandons `first`
    abandoned_view = signed_in.get("/api/session", params={"session_id": first["session_id"]})
    assert abandoned_view.status_code == 200
    assert abandoned_view.json()["mode"] == "lesson_practice"

    unknown = signed_in.get("/api/session", params={"session_id": "not-a-real-session"})
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "unknown session"}


def test_get_session_by_id_refuses_another_users_session(client):
    sign_in(client, "alice")
    complete_lesson(client, "first")
    stolen_id = practice(client, "first")["session_id"]
    assert client.post("/api/logout").status_code == 204
    sign_in(client, "bob")
    assert client.get("/api/session", params={"session_id": stolen_id}).status_code == 404


def test_answers_into_another_users_session_are_invalid(client):
    sign_in(client, "alice")
    complete_lesson(client, "first")
    payload = practice(client, "first")
    step = payload["steps"][0]
    client.post("/api/logout")
    sign_in(client, "bob")
    result = answer(client, payload["session_id"], step, right(client, step))
    assert (result["accepted"], result["rejected"]) == (False, "invalid")
    assert result["session"] is None


def test_a_buffered_answer_into_an_abandoned_session_is_accepted_but_does_not_reopen_it(signed_in):
    complete_lesson(signed_in, "first")
    complete_lesson(signed_in, "second")
    first = practice(signed_in, "first")
    step = first["steps"][0]
    practice(signed_in, "second")  # abandons `first`

    result = answer(signed_in, first["session_id"], step, right(signed_in, step))
    assert result["accepted"] is True
    still_abandoned = api_db(signed_in).study_session(api_user_id(signed_in), first["session_id"])
    assert still_abandoned["status"] == "abandoned"
    stored = api_db(signed_in).fetch_all("SELECT rating FROM events WHERE step_id=?", (step["id"],))
    assert stored[0]["rating"] is not None


# ------------------------------------------------------------------------ FSRS
def test_a_correct_primary_check_in_practice_does_not_move_the_schedule(signed_in):
    complete_lesson(signed_in, "first")
    payload = practice(signed_in, "first")
    step = payload["steps"][0]
    before = state_of(signed_in, step["card_id"])
    answer(signed_in, payload["session_id"], step, right(signed_in, step))
    after = state_of(signed_in, step["card_id"])
    assert after.due == before.due, "a correct answer in practice must not stretch the interval"
    assert after.reps == before.reps


def test_a_missed_primary_check_in_practice_applies_again(signed_in):
    complete_lesson(signed_in, "first")
    payload = practice(signed_in, "first")
    step = choice_of(payload)
    before = state_of(signed_in, step["card_id"])
    result = answer(signed_in, payload["session_id"], step, wrong(signed_in, step))
    assert result["correct"] is False
    after = state_of(signed_in, step["card_id"])
    assert after.lapses == before.lapses + 1
    assert after.due < before.due, "a miss in practice must pull the card back, not push it out"


def test_flash_after_a_practice_miss_does_not_move_the_schedule(signed_in):
    complete_lesson(signed_in, "first")
    payload = practice(signed_in, "first")
    step = choice_of(payload)
    result = answer(signed_in, payload["session_id"], step, wrong(signed_in, step))
    after_miss = state_of(signed_in, step["card_id"])

    flash = next(s for s in result["session"]["steps"] if s["kind"] == "flash")
    answer(signed_in, payload["session_id"], flash, "remembered")
    after_flash = state_of(signed_in, step["card_id"])
    assert after_flash.due == after_miss.due
    assert after_flash.reps == after_miss.reps


def test_a_reset_card_is_relearned_in_its_lesson_and_seeds_a_schedule(signed_in):
    """A card whose answer changed is new again: the lesson, not a replay, serves it."""
    complete_lesson(signed_in, "first")

    def edit(program):
        card = program.chapters[0].lessons[0].cards[1]
        assert card.id == "powerhouse-organelle"
        card.answer = "The mitochondria"
        card.option = "Mitochondria"

    upsert_edited(signed_in, edit)
    assert state_of(signed_in, "powerhouse-organelle").state == "new"
    payload = practice(signed_in, "first")
    assert payload["mode"] == "lesson"
    step = next(
        s for s in payload["steps"] if s["card_id"] == "powerhouse-organelle" and s["id"].startswith("p1:")
    )
    answer(signed_in, payload["session_id"], step, "Mitochondria")
    assert state_of(signed_in, "powerhouse-organelle").state != "new"


def test_a_forged_correct_verdict_in_practice_does_not_move_the_schedule(signed_in):
    complete_lesson(signed_in, "first")
    payload = practice(signed_in, "first")
    step = choice_of(payload)
    before = state_of(signed_in, step["card_id"])
    result = answer(signed_in, payload["session_id"], step, wrong(signed_in, step), correct=True)
    assert result["correct"] is False
    assert state_of(signed_in, step["card_id"]).lapses == before.lapses + 1


# --------------------------------------------------------------------- readiness
def test_a_miss_in_practice_makes_the_card_unready_until_the_next_primary_check(signed_in):
    complete_lesson(signed_in, "first")
    assert ready_card_ids(signed_in) == {"capital-of-france", "powerhouse-organelle"}

    payload = practice(signed_in, "first")
    session_id = payload["session_id"]
    step = choice_of(payload, "capital-of-france")
    result = answer(signed_in, session_id, step, wrong(signed_in, step))
    assert step["card_id"] not in ready_card_ids(signed_in), "a miss must drop readiness immediately"

    ladder = next(
        s for s in result["session"]["steps"] if s["id"].startswith("v2:") and s["card_id"] == step["card_id"]
    )
    after_rung = answer(signed_in, session_id, ladder, right(signed_in, ladder))["session"]
    drain(signed_in, after_rung)
    assert step["card_id"] not in ready_card_ids(signed_in), "a correct ladder rung must not return readiness"
    assert api_db(signed_in).study_session(api_user_id(signed_in), session_id)["status"] == "completed"

    # The primary check is once per session; a fresh one needs a new session.
    again = practice(signed_in, "first")
    assert again["session_id"] != session_id
    fresh = next(s for s in again["steps"] if s["card_id"] == step["card_id"] and s["id"].startswith("p1:"))
    answer(signed_in, again["session_id"], fresh, right(signed_in, fresh))
    assert step["card_id"] in ready_card_ids(signed_in)


def test_retention_metric_excludes_practice_but_problem_cards_include_it(signed_in):
    complete_lesson(signed_in, "first")
    before = signed_in.get("/api/progress").json()
    assert before["problem_cards"] == []

    payload = practice(signed_in, "first")
    step = choice_of(payload)
    answer(signed_in, payload["session_id"], step, wrong(signed_in, step))

    after = signed_in.get("/api/progress").json()
    assert after["checks_30d"] == before["checks_30d"], "practice checks do not count towards retention"
    assert after["retention_30d"] == before["retention_30d"]
    assert step["card_id"] in {card["id"] for card in after["problem_cards"]}


# -------------------------------------------------------------------- day plan
def test_a_practice_miss_is_folded_into_the_frozen_day_plan(signed_in):
    complete_lesson(signed_in, "first")
    complete_lesson(signed_in, "second")
    before_due = signed_in.get("/api/chapters").json()["review_due"]

    payload = practice(signed_in, "first")
    step = choice_of(payload, "powerhouse-organelle")
    answer(signed_in, payload["session_id"], step, wrong(signed_in, step))
    after = signed_in.get("/api/chapters").json()
    assert after["review_due"] == before_due + 1
    assert after["review_sessions_remaining"] == 1

    # A second primary miss on the same card (in a new session) must not duplicate the entry.
    practice(signed_in, "second")  # abandons the first replay
    again = practice(signed_in, "first")
    assert again["session_id"] != payload["session_id"]
    answer(signed_in, again["session_id"], choice_of(again, "powerhouse-organelle"), wrong(signed_in, step))
    assert signed_in.get("/api/chapters").json()["review_due"] == after["review_due"]


def test_a_practice_miss_before_any_freeze_needs_no_special_handling(signed_in):
    complete_lesson(signed_in, "first")
    payload = practice(signed_in, "first")
    step = choice_of(payload)
    answer(signed_in, payload["session_id"], step, wrong(signed_in, step))  # nothing frozen yet today
    assert signed_in.get("/api/chapters").json()["review_due"] >= 1  # the first freeze happens here


# ------------------------------------------------------------------ mixed practice
def test_mixed_practice_pool_excludes_new_and_unfinished_lessons(signed_in):
    complete_lesson(signed_in, "first")
    signed_in.post("/api/lessons/second/start")  # left unfinished on purpose
    started = signed_in.post("/api/practice/start")
    assert started.status_code == 200, started.text
    study = api_db(signed_in).in_progress_practice(api_user_id(signed_in))
    assert set(json.loads(study["card_ids_json"])) == {"capital-of-france", "powerhouse-organelle"}


def test_mixed_practice_is_capped_at_ten_and_available_with_unfinished_lessons_and_no_due(
    make_client, tmp_path
):
    client = numbers_client(make_client, tmp_path)
    for lesson in range(1, LESSON_COUNT):
        complete_lesson(client, f"lesson-{lesson}")
    client.post(f"/api/lessons/lesson-{LESSON_COUNT}/start")  # an unfinished lesson must not block practice
    view = client.get("/api/chapters").json()
    assert view["review_due"] == 0
    assert view["practice_available"] is True
    assert view["practice_card_count"] == PRACTICE_CARDS_PER_SESSION

    started = client.post("/api/practice/start")
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["mode"] == "mixed_practice"
    assert body["lesson_id"] is None
    assert len({step["card_id"] for step in body["steps"]}) == PRACTICE_CARDS_PER_SESSION


def test_mixed_practice_pool_smaller_than_ten_uses_the_whole_pool(signed_in):
    complete_lesson(signed_in, "first")
    started = signed_in.post("/api/practice/start").json()
    assert {step["card_id"] for step in started["steps"]} == {"capital-of-france", "powerhouse-organelle"}


def test_a_correct_answer_moves_a_card_off_the_top_of_the_next_pool(make_client, tmp_path):
    client = numbers_client(make_client, tmp_path)
    for lesson in range(1, LESSON_COUNT + 1):
        complete_lesson(client, f"lesson-{lesson}")

    first = client.post("/api/practice/start").json()
    step = first["steps"][0]
    target = step["card_id"]
    answer(client, first["session_id"], step, right(client, step))

    database, user = api_db(client), api_user_id(client)
    assert len(database.mixed_practice_candidate_cards(user)) > PRACTICE_CARDS_PER_SESSION
    next_pool = mixed_practice_pool(database, user, PRACTICE_CARDS_PER_SESSION)
    assert next_pool[0] != target, "a card just answered correctly must not stay first in line"


def test_mixed_practice_with_no_completed_lessons_is_refused(signed_in):
    response = signed_in.post("/api/practice/start")
    assert response.status_code == 404
    assert response.json() == {"detail": "nothing to practice"}


# --------------------------------------------------------------------- step ids
def test_practice_step_ids_pass_the_engine_own_validation(signed_in):
    complete_lesson(signed_in, "first")
    payload = practice(signed_in, "first")
    user = api_user_id(signed_in)
    for step in payload["steps"]:
        row = api_db(signed_in).card_for_user(user, step["card_id"])
        assert step_id_matches(step["id"], step["kind"], row)


def test_mode_and_title_are_preserved_across_answers(signed_in):
    complete_lesson(signed_in, "first")
    payload = practice(signed_in, "first")
    assert payload["title"] == "Practice: First lesson"
    step = payload["steps"][0]
    result = answer(signed_in, payload["session_id"], step, right(signed_in, step))
    assert result["session"]["mode"] == "lesson_practice"
    assert result["session"]["title"] == "Practice: First lesson"

    mixed = signed_in.post("/api/practice/start")
    assert mixed.status_code == 200
    assert mixed.json()["title"] == "Practice"
