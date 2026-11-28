"""Print one version's CHANGELOG.md section, for use as GitHub release notes.

usage: release_notes.py VERSION [CHANGELOG]

Exits 1 if the section is missing, empty or duplicated. publish.yml runs this
before anything is uploaded, so a bad changelog cannot fail a release afterwards.
"""

import re
import sys
from pathlib import Path
from typing import List


def extract(changelog: str, version: str) -> str:
    """Return the body under ``## [version]``, without the heading itself."""
    heading = re.compile(rf"## \[{re.escape(version)}\](\s+-\s.*)?\s*")
    sections: List[List[str]] = []
    current: List[str] = []
    collecting = fenced = False
    for line in changelog.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        # A section ends at the next version heading or at the link definitions.
        elif not fenced and (line.startswith("## ") or re.match(r"\[[^\]]+\]:\s", line)):
            collecting = heading.fullmatch(line) is not None
            if collecting:
                current = []
                sections.append(current)
            continue
        if collecting:
            current.append(line)
    if not sections:
        raise ValueError(f"CHANGELOG has no section '## [{version}]'")
    if len(sections) > 1:
        raise ValueError(f"CHANGELOG has {len(sections)} sections for {version}")
    body = "\n".join(sections[0]).strip()
    if not any(line.strip() and not line.startswith("#") for line in body.splitlines()):
        raise ValueError(f"CHANGELOG section for {version} is empty")
    return body + "\n"


def main(argv: List[str]) -> int:
    if len(argv) not in (1, 2):
        print("usage: release_notes.py VERSION [CHANGELOG]", file=sys.stderr)
        return 2
    path = Path(argv[1] if len(argv) == 2 else "CHANGELOG.md")
    try:
        notes = extract(path.read_text(encoding="utf-8"), argv[0])
    except (OSError, ValueError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
