"""Course content: the card contract, the YAML loader and the build-time gate.

A course is a `program.yaml` plus one `chapters/<id>.yaml` file per chapter.
Cards are authored by a coding agent, so the contract is strict and every
rejection says exactly where the problem is and how to fix it: a malformed
card must fail the build, never reach a learner.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Hashable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

# Exercise kinds that check an answer in a closed way (the learner picks or
# arranges given material). They form the ladder a card climbs, in this order.
CLOSED_KINDS = ("choice", "cloze", "assemble")

# A lesson is one sitting; more cards than this stops being one.
LESSON_MIN_CARDS = 1
LESSON_MAX_CARDS = 10

ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
# Review and practice sessions reuse the lesson id as their own session id.
RESERVED_LESSON_PREFIXES = ("review-", "practice-")

# A choice question always shows four buttons: the right one and three lures.
DISTRACTOR_COUNT = 3

# A gap needs enough of the sentence left standing to still be a sentence.
CLOZE_MIN_CONTEXT_WORDS = 3
# And enough lures shaped like the gap that the choice is about the fact,
# not about which button is the only one that fits grammatically.
CLOZE_MIN_MATCHING_LURES = 2
# Assemble needs enough blocks to be work, and few enough to fit one screen.
ASSEMBLE_MIN_WORDS = 4
ASSEMBLE_MAX_WORDS = 8

# A right button much longer than every lure gives itself away.
OPTION_LENGTH_RATIO = 2

PROGRAM_FILE = "program.yaml"
CHAPTERS_DIR = "chapters"

ContentId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
_ID_RE = re.compile(ID_PATTERN)


# ------------------------------------------------------------------ text tools
def normalize_text(value: str) -> str:
    """Compare text without case, accents or repeated whitespace.

    The same fixed rule is used for every comparison in the app, so a learner is
    never marked wrong for typing "cafe" instead of "Café".
    """
    decomposed = unicodedata.normalize("NFD", value.casefold().strip())
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.split())


def _fold_with_offsets(value: str) -> tuple[str, list[int]]:
    """Normalize `value` and remember, for every output character, its source index.

    A cloze gap must cut the original, accented, correctly cased span out of the
    answer, while finding that span has to ignore case and accents.
    """
    folded: list[str] = []
    offsets: list[int] = []
    previous_space = False
    for index, char in enumerate(value):
        if char.isspace():
            if previous_space or not folded:
                continue
            folded.append(" ")
            offsets.append(index)
            previous_space = True
            continue
        previous_space = False
        for piece in unicodedata.normalize("NFD", char.casefold()):
            if unicodedata.combining(piece):
                continue
            folded.append(piece)
            offsets.append(index)
    while folded and folded[-1] == " ":
        folded.pop()
        offsets.pop()
    return "".join(folded), offsets


def _is_boundary(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return True
    return not (text[index - 1].isalnum() and text[index].isalnum())


def find_key_span(answer: str, key: str) -> tuple[int, int] | None:
    """Locate `key` inside `answer` on word boundaries, ignoring case and accents.

    Returns the span in the original `answer`, so "cat" is not found in "catalog".
    """
    folded_answer, offsets = _fold_with_offsets(answer)
    folded_key, _ = _fold_with_offsets(key)
    if not folded_key:
        return None
    start = folded_answer.find(folded_key)
    while start != -1:
        end = start + len(folded_key)
        if _is_boundary(folded_answer, start) and _is_boundary(folded_answer, end):
            last = offsets[end - 1] + 1
            # Accents typed as separate combining marks belong to the letter before them.
            while last < len(answer) and unicodedata.combining(answer[last]):
                last += 1
            return offsets[start], last
        start = folded_answer.find(folded_key, start + 1)
    return None


def _word_count(value: str) -> int:
    return len(value.split())


# ---------------------------------------------------------------------- model
class Exercises(BaseModel):
    """Which exercise kinds a program offers. `flash` is always on and not listed."""

    model_config = ConfigDict(extra="forbid")

    triage: bool = True
    choice: bool = True
    cloze: bool = True
    assemble: bool = False


class ExercisesOverride(BaseModel):
    """A chapter's changes to the program defaults; unset kinds inherit."""

    model_config = ConfigDict(extra="forbid")

    triage: bool | None = None
    choice: bool | None = None
    cloze: bool | None = None
    assemble: bool | None = None


def _not_blank(value: str, name: str | None) -> str:
    if not value.strip():
        raise ValueError(f"{name}: must not be blank")
    return value


class Card(BaseModel):
    """One thing to learn: a prompt, its answer and optional material for exercises."""

    model_config = ConfigDict(extra="forbid")

    id: ContentId
    prompt: str
    prompt_variants: list[str] = Field(default_factory=list)
    answer: str
    option: str | None = None
    distractors: list[str] = Field(default_factory=list)
    accepted: list[str] | None = None
    key_mode: Literal["any_of", "all_of"] = "any_of"
    hint: str = ""
    note: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = ""

    @field_validator("prompt", "answer", "option")
    @classmethod
    def _text_not_blank(cls, value: str | None, info: ValidationInfo) -> str | None:
        return value if value is None else _not_blank(value, info.field_name)

    @field_validator("prompt_variants", "distractors", "accepted")
    @classmethod
    def _items_not_blank(cls, value: list[str] | None, info: ValidationInfo) -> list[str] | None:
        if value is None:
            return value
        for item in value:
            _not_blank(item, info.field_name)
        if info.field_name == "accepted" and not value:
            raise ValueError("accepted: provide at least one key or omit the field")
        return value

    @field_validator("distractors")
    @classmethod
    def _three_or_none(cls, value: list[str]) -> list[str]:
        if len(value) not in (0, DISTRACTOR_COUNT):
            raise ValueError(
                f"distractors: provide exactly {DISTRACTOR_COUNT} or omit the field (got {len(value)})"
            )
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Card:
        if self.distractors:
            # A lure that is also a right answer would mark a correct learner wrong.
            # Checked before plain distinctness so the specific breach is reported.
            correct = {normalize_text(self.answer), normalize_text(self.effective_option)}
            correct.update(normalize_text(key) for key in self.effective_accepted)
            for distractor in self.distractors:
                if normalize_text(distractor) in correct:
                    raise ValueError(f"distractors: '{distractor}' is also a correct answer to this card")
            # Two buttons that read the same after normalization cannot be told apart,
            # so the learner would have no way to choose between them.
            normalized = [normalize_text(value) for value in (self.effective_option, *self.distractors)]
            if len(set(normalized)) != len(normalized):
                raise ValueError(
                    "options must be distinct: option and distractors collide after normalization"
                )
        if self.key_mode == "all_of":
            # all_of claims "the answer is exactly this set", so every key must be
            # present; otherwise the mode is decoration and enumerations get scored
            # one element at a time.
            missing = [key for key in self.effective_accepted if find_key_span(self.answer, key) is None]
            if missing:
                raise ValueError(
                    f"key_mode all_of requires every accepted key in the answer; missing {', '.join(missing)}"
                )
        return self

    # ---------------------------------------------------------- derived data
    @property
    def effective_option(self) -> str:
        """The text of the correct button."""
        return self.option if self.option is not None else self.answer

    @property
    def effective_accepted(self) -> list[str]:
        """Answer keys: what cloze cuts out and what `all_of` checks for."""
        return list(self.accepted) if self.accepted is not None else [self.effective_option]

    @property
    def cloze_key(self) -> str | None:
        """The key cut out of the answer, when the card's data supports a gap.

        Only `any_of` cards qualify: cutting one element of an enumeration leaves
        the others on screen, so the gap would have no single right filler.
        The remainder must still read as a sentence and must not contain the key
        (otherwise the answer is printed next to its own gap). The distractors
        must also fit the gap: they are written for whole buttons, so on many
        cards they have a different shape than the cut span and the one option
        that fits is the right one.
        """
        if self.key_mode != "any_of":
            return None
        for key in self.effective_accepted:
            span = find_key_span(self.answer, key)
            if span is None:
                continue
            remainder = self.answer[: span[0]] + " " + self.answer[span[1] :]
            if _word_count(remainder) < CLOZE_MIN_CONTEXT_WORDS:
                continue
            cut = self.answer[span[0] : span[1]]
            if find_key_span(remainder, cut) is not None:
                continue
            if len(self._lures_for_gap(cut)) < CLOZE_MIN_MATCHING_LURES:
                continue
            return cut
        return None

    def _lures_for_gap(self, cut: str) -> list[str]:
        width = _word_count(cut)
        return [lure for lure in self.distractors if _word_count(lure) == width]

    @property
    def cloze_options(self) -> list[str]:
        """The buttons for the gap: the key plus this card's own lures of the same length."""
        key = self.cloze_key
        if key is None:
            return []
        return [key, *self._lures_for_gap(key)]

    def available(self, kind: str) -> bool:
        """Whether the card's data can support an exercise of this kind."""
        if kind in ("triage", "flash"):
            return True
        if kind == "choice":
            return len(self.distractors) == DISTRACTOR_COUNT
        if kind == "cloze":
            return self.cloze_key is not None
        if kind == "assemble":
            return ASSEMBLE_MIN_WORDS <= _word_count(self.answer) <= ASSEMBLE_MAX_WORDS
        raise ValueError(f"unknown exercise kind '{kind}'")

    def check_hash(self) -> str:
        """Identity of the check a learner passed.

        Wording of the prompt, hints and notes is deliberately excluded: adding a
        paraphrase must not reset the schedule of a card that is already learned.
        The answer material is included: editing it makes the card a different
        check than the one that was passed. The order of distractors and accepted
        keys is excluded: reshuffling the lures does not change the question, so
        it must not reset what learners already know.
        """
        payload = json.dumps(
            {
                "answer": self.answer,
                "effective_option": self.effective_option,
                "distractors": sorted(self.distractors),
                "effective_accepted": sorted(self.effective_accepted),
                "key_mode": self.key_mode,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Lesson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ContentId
    title: str
    cards: list[Card]

    @field_validator("id")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value.startswith(RESERVED_LESSON_PREFIXES):
            raise ValueError(f"id: '{value}' uses a reserved prefix ({', '.join(RESERVED_LESSON_PREFIXES)})")
        return value

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        return _not_blank(value, "title")

    @field_validator("cards")
    @classmethod
    def _card_count(cls, value: list[Card]) -> list[Card]:
        if not LESSON_MIN_CARDS <= len(value) <= LESSON_MAX_CARDS:
            raise ValueError(
                f"a lesson needs {LESSON_MIN_CARDS} to {LESSON_MAX_CARDS} cards (got {len(value)})"
            )
        return value


class Chapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ContentId
    title: str
    exercises: ExercisesOverride = Field(default_factory=ExercisesOverride)
    lessons: list[Lesson]

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        return _not_blank(value, "title")

    @field_validator("lessons")
    @classmethod
    def _has_lessons(cls, value: list[Lesson]) -> list[Lesson]:
        if not value:
            raise ValueError("lessons: a chapter needs at least one lesson")
        return value


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desired_retention: float = Field(default=0.9, ge=0.7, le=0.99)
    max_interval_days: int = Field(default=365, ge=1, le=36500)
    timezone: str = "UTC"
    # Reviews done before this hour still count for the previous learning day.
    day_starts_at_hour: int = Field(default=4, ge=0, le=23)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError, OSError) as error:
            raise ValueError(f"timezone: unknown time zone '{value}'") from error
        return value


class _ProgramHeader(BaseModel):
    """Fields of `program.yaml` that are the same on disk and in memory."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    title: str
    description: str = ""
    language: str = "en"
    exercises: Exercises = Field(default_factory=Exercises)
    schedule: Schedule = Field(default_factory=Schedule)

    @field_validator("title", "language")
    @classmethod
    def _text_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _not_blank(value, info.field_name)


class _ProgramFile(_ProgramHeader):
    """`program.yaml` as written: chapters are listed by id, in course order."""

    chapters: list[ContentId]

    @field_validator("chapters")
    @classmethod
    def _chapter_list(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("chapters: list at least one chapter")
        duplicates = sorted({chapter for chapter in value if value.count(chapter) > 1})
        if duplicates:
            raise ValueError(f"chapters: duplicate chapter id {', '.join(repr(d) for d in duplicates)}")
        return value


class Program(_ProgramHeader):
    chapters: list[Chapter]

    def _chapter(self, chapter_id: str) -> Chapter:
        for chapter in self.chapters:
            if chapter.id == chapter_id:
                return chapter
        raise KeyError(chapter_id)

    def exercises_for(self, chapter_id: str) -> Exercises:
        """Program defaults with the chapter's overrides applied."""
        override = self._chapter(chapter_id).exercises
        merged = self.exercises.model_dump()
        merged.update(override.model_dump(exclude_none=True))
        return Exercises(**merged)

    def rungs(self, chapter_id: str, card: Card) -> list[str]:
        """The closed exercises a card climbs in this chapter, in ladder order."""
        exercises = self.exercises_for(chapter_id)
        return [kind for kind in CLOSED_KINDS if getattr(exercises, kind) and card.available(kind)]

    @property
    def program_version(self) -> str:
        """Short fingerprint of everything the program says, for health checks."""
        canonical = json.dumps(
            program_to_dict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def program_to_dict(program: Program) -> dict:
    return program.model_dump(mode="json")


# ----------------------------------------------------------------- validation
class ContentError(Exception):
    """Content that breaks the contract; `errors` lists every problem found."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # chapter id -> {"cards": n, "choice": n, "cloze": n, "assemble": n}: how many
    # cards can support each closed exercise, whether or not it is enabled.
    coverage: dict[str, dict[str, int]] = field(default_factory=dict)


def _gate_message(card: Card, exercises: Exercises) -> str:
    if card.available("choice"):
        first = "enable choice"
    else:
        first = f"add {DISTRACTOR_COUNT} distractors to enable choice"
    if exercises.assemble:
        second = f"give the answer {ASSEMBLE_MIN_WORDS}-{ASSEMBLE_MAX_WORDS} words to enable assemble"
    else:
        second = "enable assemble"
    return f"no enabled closed exercise is possible — {first}, or {second}"


def validate_program(program: Program) -> ValidationReport:
    """Rules that span cards and chapters: unique ids, the exercise gate, warnings."""
    report = ValidationReport()
    chapter_ids: set[str] = set()
    lesson_owner: dict[str, str] = {}
    card_owner: dict[str, str] = {}

    for chapter in program.chapters:
        if chapter.id in chapter_ids:
            report.errors.append(f"{PROGRAM_FILE}: duplicate chapter id '{chapter.id}'")
            continue
        chapter_ids.add(chapter.id)
        file = f"{CHAPTERS_DIR}/{chapter.id}.yaml"
        exercises = program.exercises_for(chapter.id)
        enabled_closed = [kind for kind in CLOSED_KINDS if getattr(exercises, kind)]
        if not enabled_closed:
            report.errors.append(
                f"{file}: no closed exercise kind is enabled — "
                f"enable choice, cloze or assemble in {PROGRAM_FILE} or in this chapter"
            )

        cards = [card for lesson in chapter.lessons for card in lesson.cards]
        coverage = {"cards": len(cards)}
        coverage.update({kind: sum(1 for card in cards if card.available(kind)) for kind in CLOSED_KINDS})
        report.coverage[chapter.id] = coverage

        for lesson in chapter.lessons:
            if lesson.id in lesson_owner:
                report.errors.append(
                    f"{file}: {lesson.id}: duplicate lesson id "
                    f"(first used in {CHAPTERS_DIR}/{lesson_owner[lesson.id]}.yaml)"
                )
            else:
                lesson_owner[lesson.id] = chapter.id
            for card in lesson.cards:
                where = f"{lesson.id}/{card.id}"
                if card.id in card_owner:
                    report.errors.append(
                        f"{file}: {where}: duplicate card id "
                        f"(first used in {CHAPTERS_DIR}/{card_owner[card.id]}.yaml)"
                    )
                else:
                    card_owner[card.id] = chapter.id
                if enabled_closed and not program.rungs(chapter.id, card):
                    report.errors.append(f"{chapter.id}/{where}: {_gate_message(card, exercises)}")
                if card.distractors:
                    option_words = _word_count(card.effective_option)
                    longest = max(_word_count(lure) for lure in card.distractors)
                    if option_words > OPTION_LENGTH_RATIO * longest:
                        report.warnings.append(
                            f"{file}: {where}: option has {option_words} words but the longest "
                            f"distractor has {longest}; the length gives the right answer away"
                        )

        for kind in enabled_closed:
            if cards and coverage[kind] == 0:
                report.warnings.append(f"{file}: {kind} is enabled but no card in this chapter supports it")
    return report


# --------------------------------------------------------------------- loading
class _DuplicateKeyError(yaml.YAMLError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    """A repeated key in a mapping is an error, not a silent "last one wins".

    Otherwise a second `answer:` pasted into a card would quietly replace the
    first and the author would never see why the card reads wrongly.
    """

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            seen = set()
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=deep)
                if isinstance(key, Hashable) and key in seen:
                    raise _DuplicateKeyError(f"duplicate key '{key}'")
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _read_yaml(path: Path, label: str) -> tuple[Any, str | None]:
    if not path.is_file():
        return None, f"{label}: file not found"
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader), None
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        return None, f"{label}: invalid YAML ({' '.join(str(error).split())})"


def _child(raw: Any, key: str, index: int) -> Any:
    """`raw[key][index]` when the raw YAML has that shape, else None."""
    items = raw.get(key) if isinstance(raw, dict) else None
    return items[index] if isinstance(items, list) and index < len(items) else None


def _item_id(item: Any, key: str, index: int) -> str:
    value = item.get("id") if isinstance(item, dict) else None
    return value if isinstance(value, str) and value else f"{key}[{index}]"


def _format_errors(label: str, raw: Any, error: ValidationError) -> list[str]:
    """Turn pydantic errors into `<file>: <lesson>/<card>: <problem>` lines."""
    lines: list[str] = []
    for item in error.errors():
        loc = list(item["loc"])
        scope: list[str] = []
        if len(loc) >= 2 and loc[0] == "lessons" and isinstance(loc[1], int):
            lesson_raw = _child(raw, "lessons", loc[1])
            scope.append(_item_id(lesson_raw, "lessons", loc[1]))
            loc = loc[2:]
            if len(loc) >= 2 and loc[0] == "cards" and isinstance(loc[1], int):
                scope.append(_item_id(_child(lesson_raw, "cards", loc[1]), "cards", loc[1]))
                loc = loc[2:]
        field_path = ""
        for part in loc:
            field_path += f"[{part}]" if isinstance(part, int) else (f".{part}" if field_path else str(part))
        kind = item["type"]
        if kind == "extra_forbidden":
            text = f"unknown field '{field_path}'"
        elif kind == "missing":
            text = f"missing required field '{field_path}'"
        elif kind == "value_error":
            text = item["msg"].removeprefix("Value error, ")
        else:
            text = f"{field_path}: {item['msg']}" if field_path else item["msg"]
        prefix = f"{label}: {'/'.join(scope)}: " if scope else f"{label}: "
        lines.append(prefix + text)
    return lines


def _listed_chapter_ids(raw: Any) -> list[str] | None:
    """Chapter ids from `program.yaml` that are safe to turn into file names."""
    if not isinstance(raw, dict) or not isinstance(raw.get("chapters"), list):
        return None
    valid = [chapter for chapter in raw["chapters"] if isinstance(chapter, str) and _ID_RE.fullmatch(chapter)]
    return list(dict.fromkeys(valid))


def _load(content_dir: Path) -> tuple[Program | None, list[str]]:
    errors: list[str] = []
    raw_program, problem = _read_yaml(content_dir / PROGRAM_FILE, PROGRAM_FILE)
    if problem:
        errors.append(problem)
    header: _ProgramFile | None = None
    if problem is None:
        try:
            header = _ProgramFile.model_validate(raw_program)
        except ValidationError as error:
            errors.extend(_format_errors(PROGRAM_FILE, raw_program, error))

    listed = _listed_chapter_ids(raw_program)
    chapters: list[Chapter] = []
    chapters_dir = content_dir / CHAPTERS_DIR
    for chapter_id in listed or []:
        label = f"{CHAPTERS_DIR}/{chapter_id}.yaml"
        path = chapters_dir / f"{chapter_id}.yaml"
        if not path.is_file():
            errors.append(f"{PROGRAM_FILE}: chapter '{chapter_id}' is listed but {label} does not exist")
            continue
        raw_chapter, problem = _read_yaml(path, label)
        if problem:
            errors.append(problem)
            continue
        try:
            chapter = Chapter.model_validate(raw_chapter)
        except ValidationError as error:
            errors.extend(_format_errors(label, raw_chapter, error))
            continue
        if chapter.id != chapter_id:
            errors.append(f"{label}: id '{chapter.id}' must match the file name '{chapter_id}'")
            continue
        chapters.append(chapter)

    if listed is not None and chapters_dir.is_dir():
        for path in sorted(chapters_dir.glob("*.yaml")):
            if path.stem not in listed:
                errors.append(f"{CHAPTERS_DIR}/{path.name}: not listed in {PROGRAM_FILE} chapters")

    if header is None or not chapters:
        return None, errors
    fields = {name: getattr(header, name) for name in _ProgramHeader.model_fields}
    program = Program(**fields, chapters=chapters)
    errors.extend(validate_program(program).errors)
    return program, errors


def load_program(content_dir: Path) -> Program:
    """Read and validate a content directory; raise `ContentError` listing every problem.

    Every file is checked on its own, so one run reports problems from all of
    them. Program-level checks (the exercise gate, id uniqueness across
    chapters) run only when `program.yaml` itself is valid.
    """
    program, errors = _load(Path(content_dir))
    if errors or program is None:
        raise ContentError(errors)
    return program
