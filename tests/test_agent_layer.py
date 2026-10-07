"""The agent layer (AGENTS.md, CLAUDE.md, skills) and the content docs must match the code.

A coding agent follows these files literally, so a command that does not exist,
a skill that cannot be found or a field the loader rejects turns into a failed
run for someone else. These tests keep the instructions honest.
"""

import re
from pathlib import Path

import pytest
import yaml

from app.content import (
    Card,
    Chapter,
    Exercises,
    Lesson,
    Schedule,
    _ProgramFile,
    load_program,
    validate_program,
)
from scripts.validate_content import format_coverage

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"
SKILLS_DIR = ROOT / ".claude" / "skills"
CONTRACT = ROOT / "docs" / "content-contract.md"
EXERCISES = ROOT / "docs" / "exercises.md"
EXPECTED_SKILLS = {"add-content", "choose-exercises", "deploy", "setup-server"}
# Commands every agent needs on day one; AGENTS.md must name each of them.
REQUIRED_COMMANDS = {"setup", "dev", "test", "e2e", "validate", "user"}
# Top-level entries whose paths, when quoted in agent instructions, must exist.
CHECKED_ROOTS = ("app/", "scripts/", "deploy/", "content/", "tests/", "migrations/", ".github/", ".claude/")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def agent_files() -> list[Path]:
    return [AGENTS, CLAUDE, CONTRACT, EXERCISES, *skill_files()]


def frontmatter(path: Path) -> dict:
    match = re.match(r"---\n(.*?)\n---\n", read(path), re.S)
    assert match, f"{path.relative_to(ROOT)}: no YAML frontmatter"
    return yaml.safe_load(match.group(1))


def code_text(markdown: str) -> str:
    """Everything inside fenced blocks and inline code spans: the parts an agent runs."""
    fenced = re.findall(r"```[^\n]*\n(.*?)```", markdown, re.S)
    prose = re.sub(r"```.*?```", "", markdown, flags=re.S)
    return "\n".join([*fenced, *re.findall(r"`([^`\n]+)`", prose)])


def makefile_targets() -> set[str]:
    return set(re.findall(r"^([a-z][\w-]*):", read(ROOT / "Makefile"), re.M))


def section(markdown: str, heading: str) -> str:
    """The text under `heading` up to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = markdown.splitlines()
    start = lines.index(heading) + 1
    in_code = False
    for index in range(start, len(lines)):
        line = lines[index]
        if line.startswith("```"):
            in_code = not in_code
        # A YAML comment inside a code block looks like a heading; it is not one.
        elif not in_code and re.match(rf"#{{1,{level}}} ", line):
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


def tables(text: str) -> list[list[str]]:
    """The backticked names in the first column of each Markdown table in `text`."""
    found: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("|"):
            if current is None:
                current = []
                found.append(current)
            name = re.match(r"\|\s*`([^`]+)`", line)
            if name:
                current.append(name.group(1))
        else:
            current = None
    return found


# ------------------------------------------------------------------- skills
def test_the_four_skills_exist():
    assert {path.parent.name for path in skill_files()} == EXPECTED_SKILLS


@pytest.mark.parametrize("path", skill_files(), ids=lambda path: path.parent.name)
def test_skill_frontmatter_names_it_and_says_when_to_use_it(path: Path):
    meta = frontmatter(path)
    assert set(meta) == {"name", "description"}
    assert meta["name"] == path.parent.name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", meta["name"])
    description = meta["description"]
    assert isinstance(description, str) and 0 < len(description) <= 1024
    # Third person ("Turns ...", "Recommends ..."), never "I ..." or "You ...".
    first_word = description.split()[0]
    assert first_word not in {"I", "You", "We", "Use"} and first_word.endswith("s"), first_word
    assert "Use when" in description


# ----------------------------------------------------------- AGENTS / CLAUDE
def test_claude_md_imports_agents_md_and_points_to_the_skills():
    text = read(CLAUDE)
    assert text.splitlines()[0] == "@AGENTS.md"
    assert ".claude/skills/" in text


def test_agents_md_lists_every_skill_by_an_existing_path():
    paths = set(re.findall(r"\.claude/skills/[\w-]+/SKILL\.md", read(AGENTS)))
    assert {Path(path).parent.name for path in paths} == EXPECTED_SKILLS
    for path in paths:
        assert (ROOT / path).is_file(), path


def test_agents_md_names_the_core_commands():
    named = set(re.findall(r"\bmake ([a-z][\w-]*)", code_text(read(AGENTS))))
    assert named >= REQUIRED_COMMANDS


@pytest.mark.parametrize("path", agent_files(), ids=lambda path: str(path.relative_to(ROOT)))
def test_every_make_target_named_exists(path: Path):
    named = set(re.findall(r"\bmake ([a-z][\w-]*)", code_text(read(path))))
    missing = named - makefile_targets()
    assert not missing, f"{path.relative_to(ROOT)} names unknown make targets: {sorted(missing)}"


@pytest.mark.parametrize("path", agent_files(), ids=lambda path: str(path.relative_to(ROOT)))
def test_quoted_repository_paths_exist(path: Path):
    quoted = re.findall(r"`([^`\s<>*{}]+)`", read(path))
    checked = [token.rstrip("/") for token in quoted if token.startswith(CHECKED_ROOTS)]
    missing = [token for token in checked if not (ROOT / token).exists()]
    assert not missing, f"{path.relative_to(ROOT)} quotes paths that do not exist: {missing}"


# ---------------------------------------------------------- content contract
def test_contract_lists_exactly_the_card_fields():
    card_tables = tables(section(read(CONTRACT), "## Card fields"))
    assert card_tables, "no field table under 'Card fields'"
    assert card_tables[0] == list(Card.model_fields)


def test_contract_lists_exactly_the_program_chapter_and_lesson_fields():
    text = read(CONTRACT)
    program, exercises, schedule = tables(section(text, "## `program.yaml`"))
    assert program == list(_ProgramFile.model_fields)
    assert exercises == list(Exercises.model_fields)
    assert schedule == list(Schedule.model_fields)
    chapter, lesson = tables(section(text, "## Chapter files: `chapters/<chapter-id>.yaml`"))
    assert chapter == list(Chapter.model_fields)
    assert lesson == list(Lesson.model_fields)


def test_contract_example_course_is_valid_and_its_coverage_is_quoted(tmp_path: Path):
    text = read(CONTRACT)
    example = section(text, "## Complete example")
    content = tmp_path / "content"
    written = []
    for block in re.findall(r"```yaml\n(.*?)```", example, re.S):
        target = re.match(r"# content/(\S+)\n", block)
        assert target, "every YAML block of the example starts with '# content/<path>'"
        path = content / target.group(1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block, encoding="utf-8")
        written.append(target.group(1))
    assert written == ["program.yaml", "chapters/planets.yaml"]

    program = load_program(content)
    report = validate_program(program)
    assert report.errors == [] and report.warnings == []
    printed = "\n".join(format_coverage(report.coverage))
    assert f"```\n{printed}\n```" in example


def test_skill_example_card_is_valid():
    """The model card in add-content passes the contract and supports every closed kind."""
    block = re.search(r"```yaml\n(.*?)```", read(SKILLS_DIR / "add-content" / "SKILL.md"), re.S)
    assert block
    card = Card.model_validate(yaml.safe_load(block.group(1))[0])
    assert [kind for kind in ("choice", "cloze", "assemble") if card.available(kind)] == [
        "choice",
        "cloze",
        "assemble",
    ]
