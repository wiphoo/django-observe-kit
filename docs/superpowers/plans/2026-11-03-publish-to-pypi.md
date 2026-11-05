# Publish django-observe-kit to PyPI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release `django-observe-kit` 0.1.0 to PyPI via the existing tag-triggered OIDC workflow.

**Architecture:** `.github/workflows/publish.yml` already runs a quality gate and `pypa/gh-action-pypi-publish` (OIDC trusted publisher, `environment: pypi`) on a `v*.*.*` tag. No new release code is needed. The plan verifies the package locally, rehearses on TestPyPI, cuts the changelog, merges to `main`, and pushes the tag. PyPI uploads are irreversible (a version can never be re-uploaded), so Tasks 1, 3 and 6 are human gates.

**Tech Stack:** uv, setuptools (`python -m build`), twine, GitHub Actions, PyPI Trusted Publishing.

---

## Preconditions (check first, do not skip)

- No `v*` tags exist yet and `pyproject.toml` has `version = "0.1.0"`, so this is the first release.
- Branch `refactor/98-urllib-url-parse` has unmerged work; it must land on `main` before tagging.
- The Dependabot PR merge loop is also writing to `main`. Finish or pause it before Task 5 so the tag points at a known commit.
- `CHANGELOG.md` has only `## [Unreleased]`.
- Repo URLs in the changelog use `wiphoo/Django-Observe_Kit`; confirm the real repo name matches the PyPI Trusted Publisher config (Task 1).

### Task 1: Confirm PyPI trusted publisher (human gate)

**Files:** none

- [ ] **Step 1: Verify the project name is free**

Run: `curl -s -o /dev/null -w "%{http_code}\n" https://pypi.org/pypi/django-observe-kit/json`
Expected: `404` (free). If `200`, stop and report: the name is taken or already released.

- [ ] **Step 2: Human configures pending publisher on pypi.org**

Account > Publishing > Add a pending publisher with: project `django-observe-kit`, owner `wiphoo`, repository `django-observe-kit`, workflow `publish.yml`, environment `pypi`. Repeat on test.pypi.org. The agent cannot do this; wait for confirmation.

- [ ] **Step 3: Confirm the GitHub `pypi` environment exists**

GitHub repo > Settings > Environments > `pypi` (add required reviewers so the upload needs a click). Wait for confirmation.

### Task 2: Verify the package builds and installs cleanly

**Files:** none (artifacts in `dist/`; confirm it is git-ignored with `git check-ignore dist`)

- [ ] **Step 1: Run the full gate**

Run: `make ci`
Expected: ruff, mypy and pytest pass with coverage >= 85.

- [ ] **Step 2: Build**

Run: `rm -rf dist && make build && ls dist`
Expected: one `django_observe_kit-0.1.0-py3-none-any.whl` and one `django_observe_kit-0.1.0.tar.gz`.

- [ ] **Step 3: Validate metadata**

Run: `uv run --with twine twine check dist/*`
Expected: `PASSED` for both files.

- [ ] **Step 4: Install the wheel in a clean venv and import it**

```bash
uv venv /tmp/oidk-verify && \
uv pip install --python /tmp/oidk-verify/bin/python dist/*.whl && \
/tmp/oidk-verify/bin/python -c "import observe_kit; print(observe_kit.__file__)"
```
Expected: prints a path under `/tmp/oidk-verify`, no ImportError. If it fails on `py.typed` or missing subpackages, fix the `[tool.setuptools]` package config in `pyproject.toml` and repeat from Step 2.

### Task 3: Rehearse on TestPyPI (human supplies token)

**Files:** none

- [ ] **Step 1: Upload to TestPyPI**

The human exports `TWINE_USERNAME=__token__` and `TWINE_PASSWORD=<testpypi token>` in their shell; never print or log it.
Run: `uv run --with twine twine upload --repository-url https://test.pypi.org/legacy/ dist/*`
Expected: `View at: https://test.pypi.org/project/django-observe-kit/0.1.0/`

- [ ] **Step 2: Install from TestPyPI and import**

```bash
uv venv /tmp/oidk-test && uv pip install --python /tmp/oidk-test/bin/python \
  --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ \
  django-observe-kit==0.1.0 && \
/tmp/oidk-test/bin/python -c "import observe_kit"
```
Expected: no error. This burns 0.1.0 on TestPyPI only, which is fine.

### Task 4: Cut the changelog (human review of wording)

**Files:**
- Modify: `CHANGELOG.md:5`

- [ ] **Step 1: Rename the section**

Replace the line `## [Unreleased]` with:

```markdown
## [Unreleased]

## [0.1.0] - 2026-11-03
```

so the existing entries sit under `0.1.0`.

- [ ] **Step 2: Commit on the release branch**

```bash
git add CHANGELOG.md
git commit -m "chore: release 0.1.0"
```

### Task 5: Land on main

**Files:** none

- [ ] **Step 1: Open and merge the PR via GitHub MCP (not `gh`):** head `refactor/98-urllib-url-parse` (or a fresh `release/0.1.0` branch), base `main`, squash. Required checks must be green.

- [ ] **Step 2: Sync local main**

Run: `git switch main && git pull --ff-only && git log -1 --oneline && grep '^version' pyproject.toml`
Expected: the squash commit, and `version = "0.1.0"`.

### Task 6: Tag and publish (human gate: irreversible)

**Files:** none

- [ ] **Step 1: Ask the user for an explicit go-ahead.** State: "Tagging v0.1.0 at <sha> triggers an irreversible PyPI upload."

- [ ] **Step 2: Tag and push**

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```
Expected: the `Publish to PyPI` workflow starts; approve the `pypi` environment when prompted.

- [ ] **Step 3: Verify**

Run: `curl -s https://pypi.org/pypi/django-observe-kit/json | python -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"`
Expected: `0.1.0`. Then install `django-observe-kit==0.1.0` in a clean venv and `import observe_kit`.

- [ ] **Step 4: Create the GitHub release for v0.1.0 via GitHub MCP**, with the `0.1.0` changelog section as the body.

## Rollback

PyPI cannot overwrite a version. If 0.1.0 is bad: yank it on pypi.org, fix, bump to 0.1.1, tag `v0.1.1`.

## Self-review

- Coverage: name check, trusted publisher, build, metadata, TestPyPI, changelog, merge, tag, verify, release notes and rollback each map to a task.
- No placeholders; commands are runnable as written (the date `2026-11-03` is today).
- Open question flagged in Preconditions: repo-name casing in changelog links vs the real repo.
