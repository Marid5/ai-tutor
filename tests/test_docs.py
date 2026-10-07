"""The documentation must hold together: links resolve, images exist, versions agree.

A broken link or a missing screenshot is the first thing a visitor notices, and
a README that names a command that does not exist costs a newcomer their first
try. These checks run over every Markdown file in the repository.
"""

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"
VALUE_LINE = (
    "Turn your notes into a spaced-repetition course — "
    "your coding agent writes the cards, AI Tutor teaches them."
)
# Course content is written in whatever language the learner studies, so it is
# exempt from the English-only rule that covers code, UI and docs.
LANGUAGE_FREE_PATHS = ("content/",)
CYRILLIC = re.compile(r"[\u0400-\u052f\u1c80-\u1c8f\u2de0-\u2dff\ua640-\ua69f]")

MD_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_REF = re.compile(r"<(?:img|a)\b[^>]*?\b(?:src|href)=\"([^\"]+)\"", re.I)
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.I)


def repository_files() -> list[Path]:
    """Files git tracks or would track (not ignored ones) that exist on disk; none outside a checkout."""
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        return []
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    paths = sorted({ROOT / name for name in listing.split("\0") if name})
    return [path for path in paths if path.is_file()]


def markdown_files() -> list[Path]:
    return [path for path in repository_files() if path.suffix == ".md"]


def without_code(markdown: str) -> str:
    """Markdown with fenced blocks and inline code removed: links there are examples, not links."""
    return re.sub(r"`[^`\n]*`", "", without_code_blocks(markdown))


def code_text(markdown: str) -> str:
    """Only fenced blocks and inline code: the commands a reader copies."""
    fenced = re.findall(r"^(?:```|~~~)[^\n]*\n(.*?)^(?:```|~~~)", markdown, re.S | re.M)
    return "\n".join([*fenced, *re.findall(r"`([^`\n]+)`", without_code_blocks(markdown))])


def without_code_blocks(markdown: str) -> str:
    return re.sub(r"^(```|~~~).*?^\1", "", markdown, flags=re.S | re.M)


def links(markdown: str) -> list[str]:
    text = without_code(markdown)
    return [*MD_LINK.findall(text), *HTML_REF.findall(text)]


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"`([^`]*)`", r"\1", heading.strip())
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\- ]", "", text.lower())
    return text.replace(" ", "-")


def anchors(markdown: str) -> set[str]:
    found: set[str] = set()
    seen: dict[str, int] = {}
    for line in without_code_blocks(markdown).splitlines():
        match = re.match(r"#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        base = slug(match.group(1))
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return found


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def test_there_is_documentation_to_check():
    if not repository_files():
        pytest.skip("not a git checkout")
    names = {rel(path) for path in markdown_files()}
    expected = {
        "README.md",
        "AGENTS.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "docs/architecture.md",
        "docs/deploy.md",
        "docs/content-contract.md",
        "docs/exercises.md",
        "deploy/setup-server.md",
    }
    assert expected <= names


@pytest.mark.parametrize("path", markdown_files(), ids=rel)
def test_relative_links_resolve(path: Path):
    broken = []
    for target in links(path.read_text(encoding="utf-8")):
        if EXTERNAL.match(target):
            continue
        file_part, _, fragment = target.partition("#")
        destination = (path.parent / unquote(file_part)).resolve() if file_part else path
        if not destination.exists():
            broken.append(f"{target} (no such file)")
            continue
        if (
            fragment
            and destination.suffix == ".md"
            and fragment not in anchors(destination.read_text(encoding="utf-8"))
        ):
            broken.append(f"{target} (no such heading)")
    assert not broken, f"{rel(path)} has broken links: {broken}"


def test_readme_opens_with_the_value_line_and_only_two_badges():
    lines = README.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# AI Tutor"
    badges = [line for line in lines[:6] if line.startswith("[![")]
    assert len(badges) == 2
    assert "actions/workflows/ci.yml/badge.svg" in badges[0]
    assert "license" in badges[1].lower()
    assert VALUE_LINE in lines


def test_readme_shows_screenshots_that_exist():
    images = [target for target in links(README.read_text(encoding="utf-8")) if target.endswith(".png")]
    assert len(images) >= 3
    for image in images:
        assert image.startswith("docs/screenshots/"), image
        assert (ROOT / image).is_file(), image


def test_readme_sections_come_in_order():
    headings = re.findall(r"^## (.+)$", without_code(README.read_text(encoding="utf-8")), re.M)
    expected = [
        "Who it's for",
        "How it works",
        "Quick start with your coding agent",
        "Manual quick start",
        "Exercise kinds",
        "Deploy",
        "Architecture",
        "Security",
        "Credits",
        "License",
    ]
    assert [heading for heading in headings if heading in expected] == expected


def test_make_targets_named_in_the_docs_exist():
    targets = set(re.findall(r"^([a-z][\w-]*):", (ROOT / "Makefile").read_text(encoding="utf-8"), re.M))
    for path in (README, ROOT / "CONTRIBUTING.md", ROOT / "docs" / "deploy.md"):
        named = set()
        # "make test lint validate" names three targets; "NAME=you" is an argument and stops the match.
        for command in re.findall(r"\bmake((?: [a-z][\w-]*)+)", code_text(path.read_text(encoding="utf-8"))):
            named |= set(command.split())
        assert named <= targets, f"{rel(path)} names unknown make targets: {sorted(named - targets)}"


def test_changelog_top_entry_is_the_current_version():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    entries = re.findall(
        r"^## \[([^\]]+)\] - (\d{4}-\d{2}-\d{2})$", CHANGELOG.read_text(encoding="utf-8"), re.M
    )
    assert entries, "CHANGELOG.md has no released version entry"
    assert entries[0][0] == version


def test_changelog_lists_what_is_planned():
    text = CHANGELOG.read_text(encoding="utf-8")
    assert re.search(r"^## Planned$", text, re.M)


def test_no_cyrillic_outside_course_content():
    offending = []
    for path in repository_files():
        name = rel(path)
        if name.startswith(LANGUAGE_FREE_PATHS):
            continue
        data = path.read_bytes()
        if b"\0" in data:  # binary: images, icons
            continue
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if CYRILLIC.search(line):
                offending.append(f"{name}:{number}")
    assert not offending, f"Cyrillic text found: {offending[:20]}"
