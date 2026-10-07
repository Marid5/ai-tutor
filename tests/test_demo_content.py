"""The demo course that ships in `content/`: it must load cleanly and show every feature.

It is the first thing a new user studies and what the README screenshots show,
so beyond passing the contract it has to keep the shape the docs promise:
three chapters, every card usable as a multiple-choice question, every enabled
exercise reachable for most cards, and assemble switched on in one chapter only.
"""

from pathlib import Path

import pytest

from app.content import CLOSED_KINDS, load_program_with_warnings, validate_program

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"


@pytest.fixture(scope="module")
def loaded():
    return load_program_with_warnings(CONTENT)


@pytest.fixture(scope="module")
def program(loaded):
    return loaded[0]


def all_cards(program):
    return [card for chapter in program.chapters for lesson in chapter.lessons for card in lesson.cards]


def test_loads_without_errors_or_warnings(loaded, program):
    _, warnings = loaded
    assert warnings == []
    report = validate_program(program)
    assert report.errors == []
    assert report.warnings == []


def test_program_header(program):
    assert program.title == "How LLMs work"
    assert program.description.strip()
    assert program.language == "en"
    assert [chapter.id for chapter in program.chapters] == ["foundations", "training", "using-llms"]
    assert program.exercises.model_dump() == {
        "triage": True,
        "choice": True,
        "cloze": True,
        "assemble": False,
    }
    assert program.schedule.desired_retention == 0.9
    assert program.schedule.max_interval_days == 365
    assert program.schedule.timezone == "UTC"
    assert program.schedule.day_starts_at_hour == 4


def test_course_size(program):
    lessons = [lesson for chapter in program.chapters for lesson in chapter.lessons]
    assert 6 <= len(lessons) <= 8
    assert 28 <= len(all_cards(program)) <= 34


def test_every_card_is_a_fair_multiple_choice_question(program):
    for card in all_cards(program):
        assert card.option is not None, card.id
        width = len(card.option.split())
        assert 1 <= width <= 6, card.id
        assert len(card.distractors) == 3, card.id
        assert all(len(lure.split()) == width for lure in card.distractors), card.id


def test_every_card_explains_itself(program):
    cards = all_cards(program)
    for card in cards:
        assert card.note.strip(), card.id
        assert card.source == "Original text", card.id
    with_hint = sum(1 for card in cards if card.hint.strip())
    assert 0.35 <= with_hint / len(cards) <= 0.65


def test_assemble_is_enabled_by_exactly_one_chapter_override(program):
    assert program.exercises.assemble is False
    overriding = [chapter.id for chapter in program.chapters if chapter.exercises.assemble is True]
    assert len(overriding) == 1
    enabled = [chapter.id for chapter in program.chapters if program.exercises_for(chapter.id).assemble]
    assert enabled == overriding


def test_each_enabled_closed_kind_covers_at_least_half_of_each_chapter(program):
    for chapter in program.chapters:
        cards = [card for lesson in chapter.lessons for card in lesson.cards]
        exercises = program.exercises_for(chapter.id)
        for kind in CLOSED_KINDS:
            if getattr(exercises, kind):
                supported = sum(1 for card in cards if card.available(kind))
                assert supported * 2 >= len(cards), f"{chapter.id}: {kind} {supported}/{len(cards)}"


def test_all_text_is_ascii():
    files = sorted(CONTENT.rglob("*.yaml"))
    assert files
    for path in files:
        assert path.read_text(encoding="utf-8").isascii(), path.name
