# Release checklist

[`publish.yml`](../.github/workflows/publish.yml) is the only way to publish to PyPI. A pushed `v*.*.*` tag runs lint, type check and unit tests, checks that the tag equals the project version, builds, runs `twine check --strict`, smoke-tests the wheel in a clean venv, and then uploads with OIDC (no API token) from the `pypi` environment. A manual run on a branch is a dry run that skips the upload; a manual run on a `v*` tag publishes.

Do not run `twine upload` or `uv publish` against PyPI from a local machine: it skips all of the above. **A version uploaded to PyPI can never be uploaded again**, even after it is deleted or yanked.

## One-time setup (repository owner)

- [ ] On PyPI, under account > Publishing, add a *pending* trusted publisher: project `django-observe-kit`, owner `wiphoo`, repository `django-observe-kit`, workflow `publish.yml`, environment `pypi`. A pending publisher does not reserve the name; the first upload claims it.
- [ ] In the GitHub repository, under Settings > Environments, create `pypi`:
  - add a required reviewer (leave "Prevent self-review" off if there is only one maintainer);
  - under "Deployment branches and tags", choose "Selected branches and tags" and add only a **tag** rule `v*`.

Do both before the first tag. If the environment does not exist, GitHub creates it without protection when the workflow first uses it.

## Prepare

- [ ] Through a pull request: set `version` in `pyproject.toml`, run `uv lock`, and move the `[Unreleased]` entries in `CHANGELOG.md` under `## [X.Y.Z] - YYYY-MM-DD` with the actual release date.
- [ ] CI is green on the merged `main` commit.
- [ ] Optional dry run: run "Publish to PyPI" manually on `main`.

## TestPyPI rehearsal (manual, optional)

`publish.yml` only targets PyPI. This uploads from your machine with a TestPyPI API token; keep the token out of the repository, logs and shell history. A version can only be uploaded once to TestPyPI too.

```bash
# from a clean checkout of the commit you intend to tag
out=$(mktemp -d)
uv build --out-dir "$out"
uvx twine check --strict "$out"/*
read -rs UV_PUBLISH_TOKEN && export UV_PUBLISH_TOKEN
uv publish --publish-url https://test.pypi.org/legacy/ "$out"/*
unset UV_PUBLISH_TOKEN
```

Fetch only this package from TestPyPI, then install it with its dependencies from PyPI. Do not add TestPyPI as an extra index: anyone can publish a package there under a dependency's name.

```bash
dl=$(mktemp -d)
uvx pip download --no-deps --index-url https://test.pypi.org/simple/ -d "$dl" django-observe-kit==X.Y.Z
uv venv "$dl/venv"
uv pip install --python "$dl/venv" "$dl"/*.whl
"$dl/venv/bin/python" -I scripts/smoke_wheel.py
```

## Publish

- [ ] Tag the reviewed `main` commit. The tag must be `v` + the project version exactly. Pushing it starts the release:

  ```bash
  git fetch origin
  git tag -a vX.Y.Z <commit> -m vX.Y.Z
  git push origin vX.Y.Z
  ```

- [ ] When the gate, build and smoke jobs pass, review the run and approve the `pypi` deployment.

## Verify

- [ ] Install from PyPI in a clean venv, from a checkout of the tag:

  ```bash
  dir=$(mktemp -d)
  uv venv "$dir/venv"
  uv pip install --no-cache --python "$dir/venv" django-observe-kit==X.Y.Z
  "$dir/venv/bin/python" -I scripts/smoke_wheel.py
  ```

- [ ] Check the project page on PyPI: description, links, and files (one wheel, one sdist).
- [ ] Create the GitHub release for `vX.Y.Z`, using that version's `CHANGELOG.md` section as the notes.

## If something goes wrong

- **The workflow fails before the upload:** nothing was published. Fix it on `main`. If the fix needs a new commit, delete the unpublished tag and tag the new commit.
- **A published release is broken:** yank it on PyPI (project > Manage > Releases > Options > Yank) and give a reason. A yanked version is skipped by installers unless it is pinned exactly. Then fix on `main`, bump the patch version and release again. Do not move or reuse the published tag, and note the yank in the changelog and the GitHub release.
