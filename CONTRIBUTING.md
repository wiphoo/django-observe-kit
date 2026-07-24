# Contributing to Django Observe Kit

Thank you for contributing! This document covers everything you need to get
started — from setting up a dev environment to opening a pull request.

## Table of contents

- [Code of conduct](#code-of-conduct)
- [What can I contribute?](#what-can-i-contribute)
- [Development setup](#development-setup)
- [Running checks](#running-checks)
- [Testing](#testing)
- [Branch and commit conventions](#branch-and-commit-conventions)
- [Pull request process](#pull-request-process)
- [Branch protection / merge rules](#branch-protection--merge-rules)
- [Release process](#release-process)
- [Labels](#labels)

---

## Code of conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating you agree to uphold it. Please report unacceptable behavior to
[wiphoo.m@toffoli.co.th](mailto:wiphoo.m@toffoli.co.th).

---

## What can I contribute?

- Bug reports and reproduction cases — open a [Bug report](https://github.com/wiphoo/Django-Observe_Kit/issues/new?template=bug_report.yml) issue.
- Feature proposals — open a [Feature request](https://github.com/wiphoo/Django-Observe_Kit/issues/new?template=feature_request.yml) issue.
- Library code (context, logging, OTel, metrics, Sentry, DRF, audit, Wagtail) — see the workflow below.
- Documentation improvements — PRs welcome.
- Test coverage — especially for PII-sanitization edge cases.

If you are planning a large change, open an issue first so we can align on
direction before you invest time writing code. Security vulnerabilities must be
reported privately — see [SECURITY.md](SECURITY.md), not a public issue.

---

## Development setup

**Prerequisites:** Python 3.10+ · [uv](https://docs.astral.sh/uv/) · Git · Docker
(for integration tests)

```bash
git clone https://github.com/YOUR_USERNAME/Django-Observe_Kit.git
cd Django-Observe_Kit
git remote add upstream https://github.com/wiphoo/Django-Observe_Kit.git
make init   # installs dependencies (uv sync --dev) and sets up pre-commit hooks
```

The dev tools (Ruff, mypy, pytest) live in `[dependency-groups]`, not an extra,
so `uv sync --dev` (run by `make init`) is what installs them.

See [`docs/README.md`](docs/README.md) for how the library is structured and how
the test suite is organized.

---

## Running checks

| Check | Command |
| --- | --- |
| Format + lint + type check | `make check` |
| Auto-fix lint & format | `make fix` |
| Ruff lint only | `uv run ruff check src tests` |
| Ruff format check | `uv run ruff format --check src tests` |
| Mypy (strict) | `uv run mypy src` |
| Full CI gate | `make ci` |

All checks must pass before a PR can be merged. Target Python 3.10+, 4-space
indentation, a 100-character line limit, and double quotes. Mypy runs in strict
mode, so add explicit type annotations for public functions and non-trivial
internals.

---

## Testing

Pytest is the test runner, with coverage enforced at `--cov-fail-under=85`.

| Suite | Command |
| --- | --- |
| Fast unit tests | `make test-unit` |
| Integration (Docker-backed) | `make integration-up` then `make test-int` |
| End-to-end | `make test-e2e` |
| Everything | `make test-all` |
| Coverage gate | `pytest --cov=observe_kit --cov-fail-under=85` (HTML report in `htmlcov/`) |

Name tests `test_*.py` and keep them close to the behavior they cover. Use
`@pytest.mark.integration` for Docker-backed tests and `@pytest.mark.e2e` for
workflow tests. Prefer parametrization for matrix cases, and add unit tests for
any new logic before relying on integration coverage.

---

## Branch and commit conventions

**Branch naming:** `<type>/<issue-number>-<short-description>`

```
fix/22-sentry-scrub-scope
feature/otel-baggage-propagation
chore/bump-dep-floors
```

**Commit messages** follow [Conventional Commits](https://www.conventionalcommits.org),
scoped to the affected area where helpful:

```
fix(logging): sanitize nested body fields
feat(metrics): add per-view duration histogram
chore(deps): bump sentry-sdk floor
```

Keep commits focused — one logical change per commit. Avoid unrelated refactors
or reformats bundled with feature work. Update [`CHANGELOG.md`](CHANGELOG.md)
when introducing user-facing behavior changes.

---

## Pull request process

1. Fork the repo and create a branch from `main`.
2. Make your changes, add or update tests, and update docs.
3. Ensure all checks pass locally (`make ci` mirrors the CI gate).
4. Open a PR against `main`. Fill in the PR template. Code owners listed in
   [`.github/CODEOWNERS`](.github/CODEOWNERS) are auto-requested for review
   based on the files you change.
5. A maintainer will review. Address feedback; the reviewer resolves threads
   when satisfied.
6. Once approved and CI is green, the maintainer **squash-merges** the PR (see
   [Branch protection / merge rules](#branch-protection--merge-rules)). The PR
   branch is deleted automatically after merge.

**PR checklist (copy into your PR description):**

- [ ] `make check`
- [ ] `make test-unit`
- [ ] `make test-int` / `make test-e2e` (when applicable)
- [ ] Docs updated (if needed)
- [ ] CHANGELOG entry added (if user-facing)

We do not require a CLA, but by submitting a PR you agree that your contribution
is licensed under the project's [MIT license](LICENSE).

---

## Branch protection / merge rules

Repository and branch-protection settings are stored as code in
`.github/settings.yml` (using the [probot/settings][settings-app] schema) and
reviewed like any other change. To enforce them, install the free **Settings**
GitHub App from <https://github.com/apps/settings> on this repository and grant
it **Administration: Read & Write** permission. The app reconciles
`.github/settings.yml` against the repository on every push to `main`.

The `main` branch is protected as follows:

- Pull requests are required — no direct pushes to `main`.
- Required status checks (each kept up to date with `main` before merging, so no
  stale-green merges): `Lint & Typecheck`, `Unit Tests (Python 3.10/3.11/3.12)`,
  and `Build & Smoke Test`.
- Squash merges only; merge-commit and rebase merges are disabled.
- Linear history is required; the PR branch is deleted automatically after merge.
- Force-pushes and branch deletions are disabled (enforced for admins too).

While the project has a single maintainer, required approvals are set to `0` so
the maintainer can merge their own PRs once CI is green. Bump
`required_approving_review_count` to `1` (and set `require_code_owner_reviews:
true`) in `.github/settings.yml` once there are additional maintainers.

### Applying the rules manually

If you prefer not to install the Settings app, apply the equivalent rules in the
GitHub UI under **Settings → Branches → Branch protection rules → main**:

- Require a pull request before merging (0 approvals while solo).
- Require status checks to pass before merging (the checks listed above); require
  branches to be up to date before merging.
- Require conversation resolution before merging.
- Require linear history.
- Do not allow force pushes; do not allow deletions.
- Include administrators.

[settings-app]: https://github.com/apps/settings

---

## Release process

Releases follow [Semantic Versioning](https://semver.org). To cut a release:

1. Update [`CHANGELOG.md`](CHANGELOG.md) and the version in `pyproject.toml`.
2. Build and verify locally: `make build` (artifacts land in `dist/`).
3. Publish with the guarded target: `make publish`.

See the packaging targets in the [`Makefile`](Makefile) and
[`README.md`](README.md) for details.

---

## Labels

Labels are defined in `.github/settings.yml` and kept in sync with this table.

| Label | Meaning |
| --- | --- |
| `type/bug` | Something is broken |
| `type/feature` | New capability |
| `type/chore` | Non-functional maintenance (CI, deps, docs) |
| `type/documentation` | Docs-only change |
| `type/refactor` | Code restructuring without behavior change |
| `area/otel` | OpenTelemetry tracing (`src/observe_kit/otel`) |
| `area/logging` | Structured logging (`src/observe_kit/logging`) |
| `area/metrics` | Prometheus metrics & health (`src/observe_kit/metrics`) |
| `area/sentry` | Sentry enrichment & PII scrubbing (`src/observe_kit/sentry`) |
| `area/drf` | Django REST Framework integration (`src/observe_kit/drf`) |
| `area/audit` | Audit logging (`src/observe_kit/audit`) |
| `area/wagtail` | Wagtail integration (`src/observe_kit/wagtail_integration`) |
| `area/ci` | CI / build / release automation |
| `priority/high` | Blocks a milestone |
| `priority/medium` | Important but not blocking |
| `priority/low` | Nice to have |
| `needs-decision` | Requires a human decision before work can proceed |
| `ready` | Ready to be picked up |
| `dependencies` | Dependency updates (Dependabot) |
| `stale` | No activity for 90 days; will be closed if it stays inactive (applied by the stale workflow) |
