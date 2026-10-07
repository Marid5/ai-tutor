"""Check that the release version is the same everywhere it is written.

Usage: check_release_version.py [repo_root]

The version in VERSION (SemVer), the newest entry of CHANGELOG.md and the
version in frontend/package.json must agree. Exits 1 when they do not, so a
release cannot be published with a stale number.
"""

import json
import re
import sys
from pathlib import Path

SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?")
CHANGELOG_ENTRY = re.compile(r"^##\s+\[([^\]]+)\]", flags=re.MULTILINE)


def read_versions(root: Path) -> dict[str, str]:
    versions = {"VERSION": (root / "VERSION").read_text(encoding="utf-8").strip()}
    entries = [
        entry
        for entry in CHANGELOG_ENTRY.findall((root / "CHANGELOG.md").read_text(encoding="utf-8"))
        if entry.lower() != "unreleased"
    ]
    if not entries:
        raise ValueError("CHANGELOG.md has no released version entry (## [x.y.z])")
    versions["CHANGELOG.md"] = entries[0]
    package = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    versions["frontend/package.json"] = str(package.get("version", ""))
    return versions


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    try:
        versions = read_versions(root)
    except (OSError, ValueError) as error:
        print(f"Cannot read the release version: {error}")
        return 1

    problems = [
        f"{source}: {version!r} is not a SemVer version"
        for source, version in versions.items()
        if not SEMVER.fullmatch(version)
    ]
    if len(set(versions.values())) > 1:
        problems.append(
            "versions differ: " + ", ".join(f"{source}={version}" for source, version in versions.items())
        )
    if problems:
        print("\n".join(problems))
        return 1
    print(f"Release version {versions['VERSION']} is consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
