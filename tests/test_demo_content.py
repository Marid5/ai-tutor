"""The course in `content/`: whatever it is, it must load cleanly; the bundled demo must show every feature.

The first test runs on any course, so a learner who extends or replaces the
demo keeps a green suite as long as the content passes the contract.

The rest pin the shape of the bundled demo ("How LLMs work"), the first thing a
new user studies and what the README screenshots show: three chapters, every
card usable as a multiple-choice question, every enabled exercise reachable for
most cards, and assemble switched on in one chapter only. They run only while
`content/` holds exactly that course, recognised by its title and card ids.
"""

from pathlib import Path

import pytest
import yaml

from app.content import CLOSED_KINDS, load_program_with_warnings, validate_program

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"

DEMO_TITLE = "How LLMs work"
DEMO_CARD_IDS = {
    # foundations
    "what-is-a-token", "rare-word-split", "what-is-an-embedding", "close-embeddings",
    "what-attention-does", "transformer-architecture", "feed-forward-block",
    "what-a-model-predicts", "probability-distribution", "generation-loop", "context-window",
    "weights-during-chat",
    # training
    "pre-training-data", "what-are-parameters", "gradient-descent", "base-model", "knowledge-cutoff",
    "what-is-fine-tuning", "instruction-tuning", "rlhf-human-input", "reward-model",
    # using-llms
    "temperature", "top-k-sampling", "what-is-a-hallucination", "why-hallucinations-happen",
    "few-shot-prompting", "system-prompt", "step-by-step-reasoning", "what-rag-does",
    "rag-without-retraining", "tool-calls", "what-is-an-eval", "llm-as-a-judge",
}  # fmt: skip


def is_bundled_demo(content: Path) -> bool:
    """Whether `content` is the demo as shipped: same title, same cards, nothing added or removed.

    Read from the raw YAML, so a broken course is reported by the generic test
    below rather than failing collection.
    """
    try:
        program = yaml.safe_load((content / "program.yaml").read_text(encoding="utf-8"))
        card_ids = set()
        for chapter_id in program["chapters"]:
            chapter_file = content / "chapters" / f"{chapter_id}.yaml"
            chapter = yaml.safe_load(chapter_file.read_text(encoding="utf-8"))
            card_ids.update(card["id"] for lesson in chapter["lessons"] for card in lesson["cards"])
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        return False
    return program.get("title") == DEMO_TITLE and card_ids == DEMO_CARD_IDS


demo_only = pytest.mark.skipif(
    not is_bundled_demo(CONTENT), reason="content/ is not the bundled demo course; demo shape not checked"
)


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


@demo_only
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


@demo_only
def test_course_size(program):
    lessons = [lesson for chapter in program.chapters for lesson in chapter.lessons]
    assert 6 <= len(lessons) <= 8
    assert 28 <= len(all_cards(program)) <= 34


@demo_only
def test_every_card_is_a_fair_multiple_choice_question(program):
    for card in all_cards(program):
        assert card.option is not None, card.id
        width = len(card.option.split())
        assert 1 <= width <= 6, card.id
        assert len(card.distractors) == 3, card.id
        assert all(len(lure.split()) == width for lure in card.distractors), card.id


@demo_only
def test_every_card_explains_itself(program):
    cards = all_cards(program)
    for card in cards:
        assert card.note.strip(), card.id
        assert card.source == "Original text", card.id
    with_hint = sum(1 for card in cards if card.hint.strip())
    assert 0.35 <= with_hint / len(cards) <= 0.65


@demo_only
def test_assemble_is_enabled_by_exactly_one_chapter_override(program):
    assert program.exercises.assemble is False
    overriding = [chapter.id for chapter in program.chapters if chapter.exercises.assemble is True]
    assert len(overriding) == 1
    enabled = [chapter.id for chapter in program.chapters if program.exercises_for(chapter.id).assemble]
    assert enabled == overriding


@demo_only
def test_each_enabled_closed_kind_covers_at_least_half_of_each_chapter(program):
    for chapter in program.chapters:
        cards = [card for lesson in chapter.lessons for card in lesson.cards]
        exercises = program.exercises_for(chapter.id)
        for kind in CLOSED_KINDS:
            if getattr(exercises, kind):
                supported = sum(1 for card in cards if card.available(kind))
                assert supported * 2 >= len(cards), f"{chapter.id}: {kind} {supported}/{len(cards)}"


@demo_only
def test_all_text_is_ascii():
    files = sorted(CONTENT.rglob("*.yaml"))
    assert files
    for path in files:
        assert path.read_text(encoding="utf-8").isascii(), path.name
