"""The `scripts/validate_content.py` command line: exit codes and output."""

import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "validate_content.py"
FIXTURE = ROOT / "tests" / "fixtures" / "content_minimal"


def run_script(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )


def broken_copy(tmp_path: Path) -> Path:
    target = tmp_path / "content"
    shutil.copytree(FIXTURE, target)
    chapter_file = target / "chapters" / "basics.yaml"
    chapter = yaml.safe_load(chapter_file.read_text(encoding="utf-8"))
    chapter["lessons"][0]["cards"][0]["distractors"] = ["Lyon", "Nice"]
    chapter_file.write_text(yaml.safe_dump(chapter), encoding="utf-8")
    return target


def test_valid_content_prints_coverage_and_exits_zero():
    result = run_script(FIXTURE)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].split() == ["chapter", "cards", "choice", "cloze", "assemble"]
    assert lines[1].split() == ["basics", "4", "3", "1", "3"]
    assert "error:" not in result.stdout


def test_broken_content_prints_error_and_exits_one(tmp_path):
    result = run_script(broken_copy(tmp_path))
    assert result.returncode == 1
    error_lines = [line for line in result.stdout.splitlines() if line.startswith("error: ")]
    assert any("chapters/basics.yaml: first/capital-of-france:" in line for line in error_lines)


def test_quiet_prints_only_problems(tmp_path):
    clean = run_script(FIXTURE, "--quiet")
    assert clean.returncode == 0
    assert clean.stdout == ""

    broken = run_script(broken_copy(tmp_path), "--quiet")
    assert broken.returncode == 1
    assert broken.stdout.splitlines()
    assert all(line.startswith(("error: ", "warning: ")) for line in broken.stdout.splitlines())


def test_warnings_are_printed_without_failing(tmp_path):
    target = tmp_path / "content"
    shutil.copytree(FIXTURE, target)
    program_file = target / "program.yaml"
    program = yaml.safe_load(program_file.read_text(encoding="utf-8"))
    program["exercises"] = {"cloze": True, "assemble": True}
    program_file.write_text(yaml.safe_dump(program), encoding="utf-8")
    chapter_file = target / "chapters" / "basics.yaml"
    chapter = yaml.safe_load(chapter_file.read_text(encoding="utf-8"))
    chapter["lessons"][0]["cards"][0]["distractors"] = ["Lyon", "Marseille Nice", "Lille Metz"]
    chapter_file.write_text(yaml.safe_dump(chapter), encoding="utf-8")

    result = run_script(target)
    assert result.returncode == 0, result.stdout
    assert any(
        line.startswith("warning: ") and "cloze is enabled" in line for line in result.stdout.splitlines()
    )


def test_missing_directory_exits_one(tmp_path):
    result = run_script(tmp_path / "nowhere")
    assert result.returncode == 1
    assert "error: program.yaml" in result.stdout
