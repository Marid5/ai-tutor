"""Validate a content directory and print how well each chapter is covered.

Usage: validate_content.py [content_dir] [--quiet]

Exits 1 when the content breaks the contract. The coverage table shows, per
chapter, how many cards can support each closed exercise; it is the input for
deciding which exercises to enable.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.content import CLOSED_KINDS, ContentError, load_program, validate_program  # noqa: E402


def format_coverage(coverage: dict[str, dict[str, int]]) -> list[str]:
    columns = ("cards", *CLOSED_KINDS)
    width = max([len("chapter"), *(len(chapter) for chapter in coverage)])
    lines = [f"{'chapter':<{width}}  " + "  ".join(f"{column:>{max(len(column), 5)}}" for column in columns)]
    for chapter, counts in coverage.items():
        cells = "  ".join(f"{counts[column]:>{max(len(column), 5)}}" for column in columns)
        lines.append(f"{chapter:<{width}}  {cells}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate AI Tutor course content.")
    parser.add_argument("content_dir", nargs="?", type=Path, default=ROOT / "content")
    parser.add_argument("--quiet", action="store_true", help="print only warnings and errors")
    args = parser.parse_args(argv)

    try:
        program = load_program(args.content_dir)
    except ContentError as error:
        for message in error.errors:
            print(f"error: {message}")
        return 1

    report = validate_program(program)
    if not args.quiet:
        print("\n".join(format_coverage(report.coverage)))
    for message in report.warnings:
        print(f"warning: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
