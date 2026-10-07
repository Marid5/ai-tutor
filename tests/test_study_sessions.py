"""Scheduled review, practice and the progress numbers, at engine level."""

from __future__ import annotations

from datetime import timedelta

from app import curriculum, session
from tests.helpers import fake_now, make_user, right_answer, seed, submit, write_program, wrong_answer


def finish_lesson(db, user, program, lesson_id, *, miss=frozenset()):
    db.start_lesson(user, lesson_id)
    for _ in range(60):
        queue = session.lesson_steps(db, user, db.lesson(lesson_id))
        if not queue:
            return
        step = queue[0]
        row = db.card_for_user(user, step["card_id"])
        wrong = step["id"].startswith("p1:") and step["card_id"] in miss
        text = wrong_answer(row, step) if wrong else right_answer(row, step)
        assert submit(db, user, program, lesson_id, step, text).status == "inserted"
    raise AssertionError("lesson did not terminate")


def make_due(db, user):
    past = (fake_now() - timedelta(days=1)).isoformat()
    with db._transaction() as conn:
        conn.execute("UPDATE card_state SET due=? WHERE user_id=? AND state != 'new'", (past, user))


def drain_study(db, user, program, study, *, now=None):
    for _ in range(60):
        queue = session.study_session_steps(db, user, db.study_session(user, study["id"]))
        if not queue:
            return
        step = queue[0]
        row = db.card_for_user(user, step["card_id"])
        assert (
            submit(db, user, program, study["id"], step, right_answer(row, step), now=now).status
            == "inserted"
        )
    raise AssertionError("session did not terminate")


def test_review_sitting_serves_due_cards_and_completes(db, tmp_path):
    program = seed(db, write_program(tmp_path))
    user = make_user(db)
    finish_lesson(db, user, program, "first")
    make_due(db, user)
    now = fake_now()
    assert session.review_day_plan_status(db, user, now, program.schedule)["due_total"] == 2
    study = session.open_review_session(db, user, now, program.schedule)
    assert study["id"] == session.review_slice_session_id(now.date().isoformat(), 1)
    queue = session.study_session_steps(db, user, study)
    assert {step["kind"] for step in queue} == {"choice"}, "no triage in review"
    drain_study(db, user, program, study)
    assert db.study_session(user, study["id"])["status"] == "completed"
    assert session.review_day_plan_status(db, user, now, program.schedule)["due_total"] == 0
    assert session.open_review_session(db, user, now, program.schedule) is None


def test_practice_correct_answer_holds_the_schedule(db, tmp_path):
    program = seed(db, write_program(tmp_path))
    user = make_user(db)
    finish_lesson(db, user, program, "first")
    before = {card: db.get_state(user, card) for card in ("capital-of-france", "largest-planet")}
    pool = session.mixed_practice_pool(db, user)
    assert sorted(pool) == ["capital-of-france", "largest-planet"]
    study = db.start_practice(user, "mixed_practice", pool, "2026-10-07")
    drain_study(db, user, program, study)
    for card, state in before.items():
        assert db.get_state(user, card) == state
    assert db.study_session(user, study["id"])["status"] == "completed"


def test_practice_miss_applies_again_and_joins_todays_plan(db, tmp_path):
    program = seed(db, write_program(tmp_path))
    user = make_user(db)
    finish_lesson(db, user, program, "first")
    finish_lesson(db, user, program, "second")
    make_due(db, user)
    with db._transaction() as conn:  # only the boiling-point card is due today
        conn.execute("UPDATE card_state SET due='2099-01-01T00:00:00+00:00' WHERE card_id != 'boiling-point'")
    now = fake_now()
    plan = session.freeze_review_day_plan(db, user, now, program.schedule)
    assert [entry["id"] for entry in plan] == ["boiling-point"]

    study = db.start_practice(user, "lesson_practice", ["capital-of-france"], "2026-10-07", lesson_id="first")
    step = session.study_session_steps(db, user, study)[0]
    row = db.card_for_user(user, "capital-of-france")
    before = db.get_state(user, "capital-of-france")
    assert submit(db, user, program, study["id"], step, wrong_answer(row, step)).correct is False
    after = db.get_state(user, "capital-of-france")
    assert after.lapses == before.lapses + 1
    assert "capital-of-france" in session.unresolved_review_cards(db, user, now, program.schedule)


def test_mixed_practice_offers_missed_cards_first(db, tmp_path):
    program = seed(db, write_program(tmp_path))
    user = make_user(db)
    finish_lesson(db, user, program, "first", miss={"largest-planet"})
    assert session.mixed_practice_pool(db, user)[0] == "largest-planet"


def test_progress_numbers_follow_current_primary_checks(db, tmp_path):
    program = seed(db, write_program(tmp_path))
    user = make_user(db)
    finish_lesson(db, user, program, "first", miss={"largest-planet"})
    metrics = curriculum.learning_metrics(db, user, fake_now(), program.schedule)
    assert metrics["checks_30d"] == 2
    assert metrics["retention_30d"] == 50
    assert metrics["cards_ready"] == 1 and metrics["cards_total"] == 3
    assert metrics["lessons_completed"] == 1
    assert metrics["problem_cards"][0]["id"] == "largest-planet"

    seed(db, write_program(tmp_path, cards={"largest-planet": {"distractors": ["Saturn", "Venus", "Mars"]}}))
    metrics = curriculum.learning_metrics(db, user, fake_now(), program.schedule)
    assert metrics["checks_30d"] == 1, "checks of the old version of a card no longer count"
    assert metrics["retention_30d"] == 100


def test_show_hint_by_default_round_trip(db):
    user = make_user(db)
    assert curriculum.show_hint_by_default(db, user) is False, "hints start behind a button"
    curriculum.set_show_hint_by_default(db, user, True)
    assert curriculum.show_hint_by_default(db, user) is True
    curriculum.set_show_hint_by_default(db, user, False)
    assert curriculum.show_hint_by_default(db, user) is False


def test_forecast_groups_by_learning_day_in_the_course_timezone(db, tmp_path):
    from app.content import Schedule

    program = seed(db, write_program(tmp_path))
    user = make_user(db)
    finish_lesson(db, user, program, "first")
    tokyo = Schedule(timezone="Asia/Tokyo", day_starts_at_hour=4)
    with db._transaction() as conn:
        # 05:00 next morning in Tokyo, though still 7 October in UTC.
        conn.execute(
            "UPDATE card_state SET due='2026-10-07T20:00:00+00:00' WHERE card_id='capital-of-france'"
        )
        # 03:30 in Tokyo on 9 October: before the 04:00 rollover, so it belongs to 8 October.
        conn.execute("UPDATE card_state SET due='2026-10-08T18:30:00+00:00' WHERE card_id='largest-planet'")
    now = fake_now()  # 19:00 in Tokyo on 7 October
    forecast = curriculum.learning_metrics(db, user, now, tokyo)["forecast_7d"]
    assert forecast == [{"day": "2026-10-08", "amount": 2}]

    with db._transaction() as conn:
        conn.execute("UPDATE card_state SET due='2026-09-01T00:00:00+00:00' WHERE card_id='largest-planet'")
    forecast = curriculum.learning_metrics(db, user, now, tokyo)["forecast_7d"]
    assert forecast == [{"day": "2026-10-07", "amount": 1}, {"day": "2026-10-08", "amount": 1}], (
        "overdue is today"
    )
