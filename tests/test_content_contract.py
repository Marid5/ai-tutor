"""The content contract: what a program may contain and what the loader rejects."""

import json
from pathlib import Path

import pytest
import yaml

from app.content import (
    Card,
    ContentError,
    ValidationReport,
    find_key_span,
    load_program,
    normalize_text,
    program_to_dict,
    validate_program,
)

FIXTURE = Path(__file__).parent / "fixtures" / "content_minimal"


# ------------------------------------------------------------------ helpers
def make_card(card_id: str = "capital-of-france", **overrides) -> dict:
    """A card that supports choice, cloze and assemble; overrides replace fields."""
    card = {
        "id": card_id,
        "prompt": "What is the capital of France?",
        "answer": "The capital city of France is Paris.",
        "option": "Paris",
        "distractors": ["Lyon", "Marseille", "Nice"],
        "accepted": ["Paris"],
    }
    card.update(overrides)
    return card


def make_chapter(chapter_id: str = "basics", cards: list[dict] | None = None, **extra) -> dict:
    chapter = {
        "id": chapter_id,
        "title": chapter_id.title(),
        "lessons": [{"id": "lesson-one", "title": "Lesson one", "cards": cards or [make_card()]}],
    }
    chapter.update(extra)
    return chapter


def make_program(chapters: tuple[str, ...] = ("basics",), **extra) -> dict:
    program = {"schema_version": 1, "title": "Test course", "chapters": list(chapters)}
    program.update(extra)
    return program


def write_program(tmp_path: Path, program: dict, chapters: dict[str, dict]) -> Path:
    """Write `program.yaml` and one `chapters/<stem>.yaml` per entry; return the content dir."""
    (tmp_path / "chapters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "program.yaml").write_text(yaml.safe_dump(program, allow_unicode=True), encoding="utf-8")
    for stem, body in chapters.items():
        (tmp_path / "chapters" / f"{stem}.yaml").write_text(
            yaml.safe_dump(body, allow_unicode=True), encoding="utf-8"
        )
    return tmp_path


def load_errors(tmp_path: Path, program: dict, chapters: dict[str, dict]) -> list[str]:
    write_program(tmp_path, program, chapters)
    with pytest.raises(ContentError) as raised:
        load_program(tmp_path)
    return raised.value.errors


def load_single_card(tmp_path: Path, card: dict, **program_extra):
    write_program(tmp_path, make_program(**program_extra), {"basics": make_chapter(cards=[card])})
    return load_program(tmp_path)


def has_error(errors: list[str], *fragments: str) -> bool:
    return any(all(fragment in error for fragment in fragments) for error in errors)


# ------------------------------------------------------------------ loading
def test_minimal_fixture_loads():
    program = load_program(FIXTURE)
    assert program.title == "Fixture course"
    assert [chapter.id for chapter in program.chapters] == ["basics"]
    basics = program.chapters[0]
    assert [lesson.id for lesson in basics.lessons] == ["first", "second"]
    cards = {card.id: card for lesson in basics.lessons for card in lesson.cards}
    assert len(cards) == 4
    assert program.rungs("basics", cards["capital-of-france"]) == ["choice", "cloze", "assemble"]
    assert program.rungs("basics", cards["powerhouse-organelle"]) == ["choice"]
    assert program.rungs("basics", cards["light-versus-sound"]) == ["assemble"]
    assert program.rungs("basics", cards["primary-colors"]) == ["choice", "assemble"]

    report = validate_program(program)
    assert isinstance(report, ValidationReport)
    assert report.errors == []
    assert report.warnings == []
    assert report.coverage == {"basics": {"cards": 4, "choice": 3, "cloze": 1, "assemble": 3}}


def test_normalize_ignores_case_accents_whitespace():
    assert normalize_text("Café  NAÏVE") == "cafe naive"
    assert normalize_text("  tab\tand\nnewline ") == "tab and newline"


def test_find_key_span_cuts_original_text_and_respects_word_boundaries():
    answer = "Visit the Café Central today"
    span = find_key_span(answer, "cafe  central")
    assert span is not None
    assert answer[span[0] : span[1]] == "Café Central"
    assert find_key_span("a catalog of cats", "cat") is None
    assert find_key_span("anything", "   ") is None


def test_find_key_span_covers_separately_typed_accents():
    answer = "I drink cafe\u0301 every day"  # "é" typed as e + combining acute
    span = find_key_span(answer, "caf\u00e9")
    assert span is not None
    assert answer[span[0] : span[1]] == "cafe\u0301"
    card = Card(
        **make_card(
            answer="Every morning she orders a Cafe\u0301 Cre\u0300me downtown",
            option="Caf\u00e9 Cr\u00e8me",
            accepted=["caf\u00e9 cr\u00e8me"],
            distractors=["Green Tea", "Black Coffee", "Orange juice squeezed"],
        )
    )
    assert card.cloze_key == "Cafe\u0301 Cre\u0300me"


@pytest.mark.parametrize("filename", ["program.yaml", "chapters/basics.yaml"])
def test_duplicate_yaml_key_rejected(tmp_path, filename):
    write_program(tmp_path, make_program(), {"basics": make_chapter()})
    path = tmp_path / filename
    path.write_text(path.read_text(encoding="utf-8") + "title: Again\n", encoding="utf-8")
    with pytest.raises(ContentError) as raised:
        load_program(tmp_path)
    assert f"{filename}: invalid YAML (duplicate key 'title')" in raised.value.errors


def test_error_paths_render_list_indices_without_dots(tmp_path):
    errors = load_errors(
        tmp_path / "program",
        make_program(chapters=("basics", 5)),
        {"basics": make_chapter()},
    )
    assert has_error(errors, "program.yaml: chapters[1]")
    assert not has_error(errors, ".[")
    card = make_card(tags=["ok", 7])
    errors = load_errors(tmp_path / "card", make_program(), {"basics": make_chapter(cards=[card])})
    assert has_error(errors, "lesson-one/capital-of-france: tags[1]:")


def test_unknown_field_rejected(tmp_path):
    errors = load_errors(
        tmp_path,
        make_program(),
        {"basics": make_chapter(cards=[make_card(difficulty="hard")])},
    )
    assert has_error(errors, "chapters/basics.yaml: lesson-one/capital-of-france:", "difficulty")


def test_invalid_timezone_rejected(tmp_path):
    errors = load_errors(
        tmp_path,
        make_program(schedule={"timezone": "Mars/Olympus_Mons"}),
        {"basics": make_chapter()},
    )
    assert has_error(errors, "program.yaml:", "timezone")


@pytest.mark.parametrize("bad_id", ["Bad_Id", "-leading-dash", "a" * 65, ""])
def test_card_id_pattern(tmp_path, bad_id):
    errors = load_errors(tmp_path, make_program(), {"basics": make_chapter(cards=[make_card(bad_id)])})
    assert has_error(errors, "lesson-one/", "id:")


def test_longest_valid_card_id_is_accepted(tmp_path):
    program = load_single_card(tmp_path, make_card("x1-" + "y" * 61))
    assert len(program.chapters[0].lessons[0].cards[0].id) == 64


# --------------------------------------------------------- distractor rules
@pytest.mark.parametrize("lures", [["Lyon"], ["Lyon", "Nice"], ["Lyon", "Nice", "Lille", "Metz"]])
def test_distractors_must_be_three_or_none(tmp_path, lures):
    card = make_card(distractors=lures)
    errors = load_errors(tmp_path, make_program(), {"basics": make_chapter(cards=[card])})
    assert has_error(errors, "distractors", "exactly 3")


def test_no_distractors_is_allowed_when_another_exercise_carries_the_card(tmp_path):
    program = load_single_card(tmp_path, make_card(distractors=[]), exercises={"assemble": True})
    assert program.chapters[0].lessons[0].cards[0].distractors == []


@pytest.mark.parametrize(
    ("card_fields", "bad_lure"),
    [
        ({"accepted": ["Paris", "the city of lights"]}, "The City of Lights"),
        ({"accepted": ["Paris"]}, "PARIS"),
        ({"option": "Paris"}, "  paris "),
        ({"accepted": ["Paris"]}, "the capital city of FRANCE is Paris."),
    ],
)
def test_distractor_equal_to_accepted_is_rejected(tmp_path, card_fields, bad_lure):
    card = make_card(distractors=["Lyon", "Nice", bad_lure], **card_fields)
    errors = load_errors(tmp_path, make_program(), {"basics": make_chapter(cards=[card])})
    assert has_error(errors, "lesson-one/capital-of-france:", "also a correct answer")


def test_options_must_be_distinct_after_normalization(tmp_path):
    card = make_card(distractors=["Lyon", "  LYON", "Nice"])
    errors = load_errors(tmp_path, make_program(), {"basics": make_chapter(cards=[card])})
    assert has_error(errors, "options must be distinct")


def test_all_of_keys_must_appear_in_answer(tmp_path):
    card = make_card(key_mode="all_of", accepted=["Paris", "Berlin"])
    errors = load_errors(tmp_path, make_program(), {"basics": make_chapter(cards=[card])})
    assert has_error(errors, "all_of", "Berlin")
    assert not has_error(errors, "Paris,")

    ok = load_single_card(tmp_path / "ok", make_card(key_mode="all_of", accepted=["paris"]))
    assert ok.chapters[0].lessons[0].cards[0].key_mode == "all_of"


# ------------------------------------------------------------- exercise kinds
def test_cloze_requires_any_of_context_and_matching_lures():
    base = Card(**make_card())
    assert base.cloze_key == "Paris"
    assert base.available("cloze")

    # An enumeration cannot be cut down to one gap.
    assert Card(**make_card(key_mode="all_of")).cloze_key is None
    # Too little sentence left around the gap.
    assert Card(**make_card(answer="It is Paris")).cloze_key is None
    # The key would still be printed next to its own gap.
    assert Card(**make_card(answer="Paris and Paris again and again")).cloze_key is None
    # Lures whose shape does not fit the gap.
    shapes = Card(**make_card(distractors=["Lyon", "the big city", "a nice town"]))
    assert shapes.cloze_key is None
    assert shapes.cloze_options == []
    assert not shapes.available("cloze")
    # No own distractors at all.
    assert Card(**make_card(distractors=[])).cloze_key is None


def test_cloze_options_are_key_plus_same_length_distractors():
    card = Card(
        **make_card(
            answer="Every morning she orders a Café Crème downtown",
            option="Café Crème",
            accepted=["cafe creme"],
            distractors=["Green Tea", "Black Coffee", "Orange juice squeezed"],
        )
    )
    assert card.cloze_key == "Café Crème"
    assert card.cloze_options == ["Café Crème", "Green Tea", "Black Coffee"]

    plain = Card(**make_card())
    assert plain.cloze_options == ["Paris", "Lyon", "Marseille", "Nice"]


@pytest.mark.parametrize(("words", "expected"), [(3, False), (4, True), (8, True), (9, False)])
def test_assemble_requires_four_to_eight_words(words, expected):
    card = Card(
        **make_card(answer=" ".join(["word"] * words), option="word", distractors=[], accepted=["word"])
    )
    assert card.available("assemble") is expected


def test_triage_and_flash_are_always_available():
    card = Card(**make_card(distractors=[], answer="Short"))
    assert card.available("triage") and card.available("flash")
    assert not card.available("choice")
    with pytest.raises(ValueError):
        card.available("typing")


def test_effective_defaults():
    card = Card(id="x", prompt="Q?", answer="An answer")
    assert card.effective_option == "An answer"
    assert card.effective_accepted == ["An answer"]
    with_option = Card(id="x", prompt="Q?", answer="An answer", option="answer")
    assert with_option.effective_accepted == ["answer"]


# --------------------------------------------------------------- identifiers
def test_duplicate_card_id_across_chapters_rejected(tmp_path):
    first = make_chapter("one", cards=[make_card("same-card")])
    second = make_chapter("two", cards=[make_card("same-card")])
    second["lessons"][0]["id"] = "lesson-two"
    errors = load_errors(tmp_path, make_program(("one", "two")), {"one": first, "two": second})
    assert has_error(
        errors, "chapters/two.yaml: lesson-two/same-card:", "duplicate card id", "chapters/one.yaml"
    )


def test_duplicate_lesson_and_chapter_ids_rejected(tmp_path):
    first = make_chapter("one", cards=[make_card("card-one")])
    second = make_chapter("two", cards=[make_card("card-two")])  # same lesson id "lesson-one"
    errors = load_errors(tmp_path / "lesson", make_program(("one", "two")), {"one": first, "two": second})
    assert has_error(errors, "duplicate lesson id", "lesson-one")

    errors = load_errors(tmp_path / "chapter", make_program(("one", "one")), {"one": first})
    assert has_error(errors, "program.yaml:", "duplicate chapter id", "one")


@pytest.mark.parametrize("lesson_id", ["review-1", "practice-final"])
def test_reserved_lesson_prefix_rejected(tmp_path, lesson_id):
    chapter = make_chapter()
    chapter["lessons"][0]["id"] = lesson_id
    errors = load_errors(tmp_path, make_program(), {"basics": chapter})
    assert has_error(errors, "chapters/basics.yaml:", "reserved")


def test_chapter_id_must_match_file_name(tmp_path):
    errors = load_errors(tmp_path, make_program(), {"basics": make_chapter("other")})
    assert has_error(errors, "chapters/basics.yaml:", "'other'", "must match the file name")


def test_unlisted_chapter_file_rejected(tmp_path):
    errors = load_errors(
        tmp_path,
        make_program(),
        {"basics": make_chapter(), "extra": make_chapter("extra")},
    )
    assert has_error(errors, "chapters/extra.yaml:", "not listed in program.yaml")


def test_missing_chapter_file_rejected(tmp_path):
    errors = load_errors(tmp_path, make_program(("basics", "ghost")), {"basics": make_chapter()})
    assert has_error(errors, "program.yaml:", "'ghost'", "chapters/ghost.yaml")


def test_lesson_card_count_limits(tmp_path):
    empty = make_chapter()
    empty["lessons"][0]["cards"] = []
    errors = load_errors(tmp_path / "empty", make_program(), {"basics": empty})
    assert has_error(errors, "chapters/basics.yaml: lesson-one:", "1 to 10 cards", "got 0")

    crowded = make_chapter(cards=[make_card(f"card-{index}") for index in range(11)])
    errors = load_errors(tmp_path / "crowded", make_program(), {"basics": crowded})
    assert has_error(errors, "1 to 10 cards", "got 11")

    full = make_chapter(cards=[make_card(f"card-{index}") for index in range(10)])
    write_program(tmp_path / "full", make_program(), {"basics": full})
    assert len(load_program(tmp_path / "full").chapters[0].lessons[0].cards) == 10


# --------------------------------------------------------- exercises and rungs
def test_chapter_override_merges_with_program_defaults(tmp_path):
    chapters = {
        "plain": make_chapter("plain", cards=[make_card("plain-card")]),
        "tuned": make_chapter(
            "tuned", cards=[make_card("tuned-card")], exercises={"choice": True, "assemble": True}
        ),
    }
    chapters["tuned"]["lessons"][0]["id"] = "lesson-tuned"
    write_program(
        tmp_path,
        make_program(("plain", "tuned"), exercises={"triage": False, "choice": False, "cloze": True}),
        chapters,
    )
    program = load_program(tmp_path)
    plain = program.exercises_for("plain")
    assert (plain.triage, plain.choice, plain.cloze, plain.assemble) == (False, False, True, False)
    tuned = program.exercises_for("tuned")
    assert (tuned.triage, tuned.choice, tuned.cloze, tuned.assemble) == (False, True, True, True)
    with pytest.raises(KeyError):
        program.exercises_for("missing")


def test_rungs_follow_ladder_order_and_toggles(tmp_path):
    card = Card(**make_card())
    cases = [
        ({}, ["choice", "cloze"]),
        ({"assemble": True}, ["choice", "cloze", "assemble"]),
        ({"choice": False, "assemble": True}, ["cloze", "assemble"]),
        ({"choice": False, "cloze": False, "assemble": True}, ["assemble"]),
        ({"cloze": False}, ["choice"]),
    ]
    for index, (exercises, expected) in enumerate(cases):
        folder = tmp_path / str(index)
        program = load_single_card(folder, make_card(), exercises=exercises)
        assert program.rungs("basics", card) == expected, exercises


# ----------------------------------------------------------------- the gate
def test_gate_fails_when_card_has_no_enabled_closed_kind(tmp_path):
    card = make_card(
        "no-lures",
        answer="A long answer that has far more than eight words so it cannot be assembled from blocks",
        distractors=[],
        accepted=None,
        option="short option",
    )
    errors = load_errors(tmp_path, make_program(), {"basics": make_chapter(cards=[card])})
    assert (
        "basics/lesson-one/no-lures: no enabled closed exercise is possible — "
        "add 3 distractors to enable choice, or enable assemble"
    ) in errors


LONG_ANSWER = "A long answer that has far more than eight words so it cannot be assembled from blocks"


def test_gate_hint_suggests_enabling_choice_when_lures_exist(tmp_path):
    card = make_card("has-lures", answer=LONG_ANSWER, accepted=None, option="short option")
    errors = load_errors(
        tmp_path,
        make_program(exercises={"choice": False}),
        {"basics": make_chapter(cards=[card])},
    )
    assert (
        "basics/lesson-one/has-lures: no enabled closed exercise is possible — "
        "enable choice, or enable assemble"
    ) in errors


def test_gate_hint_asks_for_four_to_eight_words_when_assemble_is_enabled(tmp_path):
    card = make_card("no-lures", answer=LONG_ANSWER, distractors=[], accepted=None, option="short option")
    errors = load_errors(
        tmp_path,
        make_program(exercises={"assemble": True}),
        {"basics": make_chapter(cards=[card])},
    )
    assert (
        "basics/lesson-one/no-lures: no enabled closed exercise is possible — "
        "add 3 distractors to enable choice, or give the answer 4-8 words to enable assemble"
    ) in errors


def test_gate_fails_when_chapter_enables_no_closed_kind(tmp_path):
    errors = load_errors(
        tmp_path,
        make_program(exercises={"choice": False, "cloze": False}),
        {"basics": make_chapter()},
    )
    assert has_error(errors, "basics", "no closed exercise kind is enabled")


def test_warning_for_enabled_kind_without_support(tmp_path):
    card = make_card("organelle", answer="Mitochondria", option=None, accepted=None)
    program = load_single_card(tmp_path, card)
    report = validate_program(program)
    assert report.errors == []
    assert any("basics" in warning and "cloze" in warning for warning in report.warnings)
    assert not any("choice" in warning for warning in report.warnings)
    assert report.coverage["basics"] == {"cards": 1, "choice": 1, "cloze": 0, "assemble": 0}


def test_warning_for_long_option(tmp_path):
    card = make_card(option="the French capital known for its famous tower", accepted=["Paris"])
    program = load_single_card(tmp_path, card)
    report = validate_program(program)
    assert report.errors == []
    assert has_error(
        report.warnings, "chapters/basics.yaml: lesson-one/capital-of-france:", "option", "8 words"
    )


def test_all_errors_reported_at_once(tmp_path):
    bad_card = make_card("bad-card", distractors=["only one"], mood="sad")
    no_exit = make_card(
        "no-exit",
        answer="An answer that is much too long to ever be assembled from blocks on one screen",
        distractors=[],
    )
    chapters = {
        "alpha": make_chapter("alpha", cards=[bad_card]),
        "beta": make_chapter("beta", cards=[no_exit]),
        "gamma": make_chapter("not-gamma"),
        "stray": make_chapter("stray"),
    }
    chapters["beta"]["lessons"][0]["id"] = "lesson-beta"
    errors = load_errors(
        tmp_path / "chapters-only", make_program(("alpha", "beta", "gamma", "missing")), chapters
    )
    assert has_error(errors, "chapters/alpha.yaml: lesson-one/bad-card:", "exactly 3")
    assert has_error(errors, "chapters/alpha.yaml: lesson-one/bad-card:", "mood")
    assert has_error(errors, "beta/lesson-beta/no-exit:", "no enabled closed exercise is possible")
    assert has_error(errors, "chapters/gamma.yaml:", "must match the file name")
    assert has_error(errors, "chapters/stray.yaml:", "not listed")
    assert has_error(errors, "program.yaml:", "'missing'")

    # A broken program.yaml does not hide the problems inside chapter files.
    errors = load_errors(
        tmp_path / "with-program-error",
        make_program(("alpha",), schedule={"day_starts_at_hour": 30}),
        {"alpha": chapters["alpha"]},
    )
    assert has_error(errors, "program.yaml:", "day_starts_at_hour")
    assert has_error(errors, "chapters/alpha.yaml: lesson-one/bad-card:", "exactly 3")


# -------------------------------------------------------- versions and hashes
def test_program_version_stable_and_content_sensitive(tmp_path):
    program = load_program(FIXTURE)
    version = program.program_version
    assert len(version) == 12
    assert set(version) <= set("0123456789abcdef")
    assert load_program(FIXTURE).program_version == version
    assert json.dumps(program_to_dict(program))  # plain JSON data

    write_program(tmp_path / "a", make_program(), {"basics": make_chapter()})
    changed = make_chapter(cards=[make_card(prompt="Which city is the capital of France?")])
    write_program(tmp_path / "b", make_program(), {"basics": changed})
    assert load_program(tmp_path / "a").program_version != load_program(tmp_path / "b").program_version


def test_check_hash_ignores_prompt_and_note():
    base = Card(**make_card())
    digest = base.check_hash()
    assert len(digest) == 64
    assert Card(**make_card()).check_hash() == digest

    for harmless in (
        {"prompt": "Name the capital of France."},
        {"prompt_variants": ["Which city is France's capital?"]},
        {"note": "Paris has been the capital for centuries."},
        {"hint": "City of lights"},
        {"tags": ["geography"]},
        {"source": "Atlas"},
    ):
        assert Card(**make_card(**harmless)).check_hash() == digest, harmless

    for meaningful in (
        {"answer": "The capital of France is Paris."},
        {"option": "Paris, France"},
        {"distractors": ["Lyon", "Marseille", "Lille"]},
        {"accepted": ["Paris", "paris, france"], "key_mode": "any_of"},
        {"key_mode": "all_of"},
    ):
        assert Card(**make_card(**meaningful)).check_hash() != digest, meaningful


def test_check_hash_ignores_order_of_distractors_and_accepted():
    base = Card(**make_card(accepted=["Paris", "the city"]))
    reordered = Card(**make_card(distractors=["Nice", "Lyon", "Marseille"], accepted=["the city", "Paris"]))
    assert reordered.check_hash() == base.check_hash()
    swapped_value = Card(**make_card(distractors=["Nice", "Lyon", "Lille"], accepted=["Paris", "the city"]))
    assert swapped_value.check_hash() != base.check_hash()
    other_key = Card(**make_card(accepted=["Paris", "a city"]))
    assert other_key.check_hash() != base.check_hash()
