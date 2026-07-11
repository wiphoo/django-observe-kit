# Contributing to Django Observe Kit

Thanks for helping improve the project! Follow these minimal steps.

## Prepare your environment
1. Fork the repo, clone it locally, and add the upstream remote:
   ```bash
   git clone https://github.com/YOUR_USERNAME/Django-Observe_Kit.git
   cd Django-Observe_Kit
   git remote add upstream https://github.com/wiphoo/Django-Observe_Kit.git
   ```
2. Install dependencies and hooks:
   ```bash
   make init
   ```
3. Browse [`docs/README.md`](docs/README.md) for how the library is structured and how the test suite is organized.

## Development workflow
- Create a feature branch (`feature/<short-description>` or `fix/<description>`).
- Make focused changes and keep commits scoped to a single intent.
- Run `make check` and `make test-unit` before pushing.
- When the work is ready, run `make pr` to rerun the quality gate, then push and open a PR against `main`.

## Testing commands
- `make test-unit` — fast unit tests
- `make test-int` (after `make integration-up`) — integration suite
- `make test-e2e` — end-to-end checks
- `make test-all` — full stack
- `pytest --cov=observe_kit --cov-fail-under=85` — coverage gate with HTML report in `htmlcov/`

## Pull requests & changelog
- Link to the relevant issue or describe the problem you are solving.
- Reference the new doc changes if you add or remove documentation.
- Update `CHANGELOG.md` when introducing user-facing behavior changes.
- Follow Conventional Commit style for commit messages (e.g., `fix(logging): sanitize body fields`).

## PR checklist (copy into your PR description)
- [ ] `make check`
- [ ] `make test-unit`
- [ ] `make test-int`/`make test-e2e` (when applicable)
- [ ] Docs updated (if needed)
- [ ] CHANGELOG entry added (if user-facing)
