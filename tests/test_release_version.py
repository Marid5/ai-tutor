"""The release version must read the same in VERSION, the changelog and the client."""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_release_version.py"


def run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        capture_output=True,
        text=True,
    )


def copy_release_files(target: Path) -> Path:
    (target / "frontend").mkdir(parents=True)
    for name in ("VERSION", "CHANGELOG.md", "frontend/package.json"):
        shutil.copy(ROOT / name, target / name)
    return target


def test_repository_versions_agree():
    result = run(ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_copy_of_the_repository_passes(tmp_path):
    assert run(copy_release_files(tmp_path / "repo")).returncode == 0


def test_mismatching_version_file_fails(tmp_path):
    repo = copy_release_files(tmp_path / "repo")
    (repo / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    result = run(repo)
    assert result.returncode == 1
    assert "9.9.9" in result.stdout + result.stderr


def test_mismatching_changelog_fails(tmp_path):
    repo = copy_release_files(tmp_path / "repo")
    changelog = repo / "CHANGELOG.md"
    changelog.write_text(changelog.read_text(encoding="utf-8").replace("0.1.0", "0.2.0"), encoding="utf-8")
    assert run(repo).returncode == 1


def test_mismatching_package_version_fails(tmp_path):
    repo = copy_release_files(tmp_path / "repo")
    package = repo / "frontend" / "package.json"
    package.write_text(package.read_text(encoding="utf-8").replace('"0.1.0"', '"0.3.0"'), encoding="utf-8")
    assert run(repo).returncode == 1


def test_version_that_is_not_semver_fails(tmp_path):
    repo = copy_release_files(tmp_path / "repo")
    (repo / "VERSION").write_text("1.0\n", encoding="utf-8")
    assert run(repo).returncode == 1


def test_missing_file_fails(tmp_path):
    repo = copy_release_files(tmp_path / "repo")
    (repo / "CHANGELOG.md").unlink()
    assert run(repo).returncode == 1
