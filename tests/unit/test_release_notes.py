"""``scripts/release_notes.py`` supplies the GitHub release notes in publish.yml."""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_notes.py"
_spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
assert _spec is not None and _spec.loader is not None
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

## [Unreleased]

## [1.2.0] - 2026-01-02

First line.

### Added

- A thing, see ## not a heading.

```bash
## a shell comment inside a fence
```

## [1.1.0] - 2025-12-01

- Older.

## [1.0.10]

- Ten.

[1.2.0]: https://example.invalid/1.2.0
"""


def test_returns_exactly_the_requested_section() -> None:
    assert release_notes.extract(CHANGELOG, "1.2.0") == (
        "First line.\n\n### Added\n\n- A thing, see ## not a heading.\n\n"
        "```bash\n## a shell comment inside a fence\n```\n"
    )


def test_last_section_stops_at_link_definitions() -> None:
    assert release_notes.extract(CHANGELOG, "1.0.10") == "- Ten.\n"


@pytest.mark.parametrize("version", ["9.9.9", "1.0.1", "1.2", "1x2x0", "Unreleased "])
def test_missing_section_fails(version: str) -> None:
    with pytest.raises(ValueError, match="no section"):
        release_notes.extract(CHANGELOG, version)


@pytest.mark.parametrize("body", ["", "\n\n", "\n### Added\n\n"])
def test_empty_section_fails(body: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        release_notes.extract(f"## [2.0.0] - 2026-01-01\n{body}\n## [1.0.0]\n\n- x\n", "2.0.0")


def test_duplicate_section_fails() -> None:
    with pytest.raises(ValueError, match="2 sections"):
        release_notes.extract("## [1.0.0]\n- a\n## [1.0.0]\n- b\n", "1.0.0")


def _run(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def test_command_line(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    found = _run("1.1.0", str(changelog))
    missing = _run("3.0.0", str(changelog))

    assert (found.returncode, found.stdout) == (0, "- Older.\n")
    assert missing.returncode == 1
    assert missing.stdout == ""
    assert "::error::" in missing.stderr and "3.0.0" in missing.stderr


def test_this_repository_can_release_its_current_version() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None
    notes = release_notes.extract(
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), match.group(1)
    )

    # Relative links break once the section is used as GitHub release notes.
    assert [u for u in re.findall(r"\]\(([^)\s]+)\)", notes) if not u.startswith("https://")] == []
