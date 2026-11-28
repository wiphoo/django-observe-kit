# Release checklist

[`publish.yml`](../.github/workflows/publish.yml) is the only way to publish to PyPI. It runs lint, type check and unit tests, extracts this version's release notes from `CHANGELOG.md` (failing if that section is missing or empty), builds, runs `twine check --strict` and smoke-tests the wheel in a clean venv. What happens next depends on how it was started:

| Trigger | Uploads |
| --- | --- |
| Push of a `v*.*.*` tag | PyPI, then a GitHub Release. The tag must equal the project version. |
| Manual run (default) | Nothing: a dry run, on a branch or a tag. |
| Manual run with "Rehearsal: upload this build to TestPyPI" ticked | TestPyPI only. Run it on `main`: the `testpypi` environment rejects every other branch and tag. |

No manual run can reach PyPI. Both uploads use OIDC trusted publishing (no API token). Pushing a branch does not start this workflow.

Do not run `twine upload` or `uv publish` against PyPI from a local machine: it skips all of the above. **A version uploaded to PyPI can never be uploaded again**, even after it is deleted or yanked.

## One-time setup (repository owner)

- [ ] On [PyPI](https://pypi.org), under account > Publishing, add a *pending* trusted publisher: project `django-observe-kit`, owner `wiphoo`, repository `django-observe-kit`, workflow `publish.yml`, environment `pypi`.
- [ ] On [TestPyPI](https://test.pypi.org) (a separate account), add the same pending publisher with environment `testpypi`.
- [ ] In the GitHub repository, under Settings > Environments, verify `pypi`:
  - the repository owner is a required reviewer ("Prevent self-review" must be off while there is only one maintainer);
  - "Deployment branches and tags" is "Selected branches and tags" with only a **tag** rule `v*`.
- [ ] Verify `testpypi`: "Deployment branches and tags" allows only the **branch** `main`. It has no required reviewer.

A pending publisher does not reserve the name; the first upload claims it. If either environment is ever deleted, recreate it with these rules before running the workflow: GitHub creates a missing environment, without protection, when a workflow first uses it.

## Repository settings (repository owner)

[`.github/settings.yml`](../.github/settings.yml) declares the description, homepage, topics, labels and `main` protection, but it only takes effect if the [Settings app](https://github.com/apps/settings) is installed. Without the app, apply them by hand:

- [ ] On the repository page, click the gear next to "About". Copy `description`, `homepage` and `topics` from `settings.yml`. After the first release, consider changing the website to `https://pypi.org/project/django-observe-kit/` (and `homepage` in `settings.yml` with it).
- [ ] Under Settings > Advanced Security, enable private vulnerability reporting, which [`SECURITY.md`](../SECURITY.md) links to.
- [ ] Protect release tags: Settings > Rules > Rulesets > New ruleset > New tag ruleset. Set enforcement to "Active", add the target pattern `v*`, enable "Restrict creations", "Restrict updates" and "Restrict deletions", and add only the maintainer (or the repository admin role) to the bypass list, so that only they can create release tags. Bypassing also allows moving and deleting them, so do that only for a tag that never published.

## Tags

- Release tags are `vX.Y.Z`, matching the project version, on the reviewed commit on `main`.
- They are annotated; sign them if you have a signing key set up (`git tag -s` instead of `git tag -a`, then check with `git tag -v`).
- Once a version has been published, never move, delete or reuse its tag.
- Any pushed `v*.*.*` tag starts the publish workflow, so never push one to try something out.
- GitHub Release notes are that version's `CHANGELOG.md` section.

## Prepare

- [ ] Through a pull request: set `version` in `pyproject.toml`, run `uv lock`, and move the `[Unreleased]` entries in `CHANGELOG.md` under `## [X.Y.Z] - YYYY-MM-DD` with the actual release date.
- [ ] CI is green on the merged `main` commit.
- [ ] Optional dry run: run "Publish to PyPI" manually on `main`.

## TestPyPI rehearsal (optional)

TestPyPI also refuses to take the same file name twice, even after deletion: **each version can be rehearsed only once.** If a rehearsal shows a problem after the upload, use a new version for the next one, for example a release candidate such as `X.Y.ZrcN`. It has to reach `main` through a pull request, with `uv lock` and its own `CHANGELOG.md` section, and another pull request sets the final version before tagging. The alternative is for the owner to deliberately add a deployment branch rule to `testpypi` for a throwaway branch, and to remove it afterwards. Otherwise rehearse the final version once, when you expect it to pass; a TestPyPI upload does not affect PyPI. A dry run costs no version.

- [ ] After the release pull request is merged, and before tagging: under Actions > "Publish to PyPI" > "Run workflow", select `main`, tick "Rehearsal: upload this build to TestPyPI" and run it.
- [ ] Verify the upload from a checkout of the same commit. Fetch only this package from TestPyPI, then install it with its dependencies from PyPI. Do not add TestPyPI as an index or extra index for the install: anyone can publish a package there under a dependency's name. `--isolated` and `PIP_CONFIG_FILE=/dev/null` matter: an `extra-index-url` in your pip configuration would otherwise still be searched. Also unset any `UV_INDEX*` or `UV_EXTRA_INDEX_URL` variables.

  ```bash
  dl=$(mktemp -d)
  PIP_CONFIG_FILE=/dev/null uvx pip --isolated download --no-deps --only-binary :all: --index-url https://test.pypi.org/simple/ -d "$dl" django-observe-kit==X.Y.Z
  uv venv "$dl/venv"
  uv pip install --no-config --default-index https://pypi.org/simple/ --python "$dl/venv" "$dl"/*.whl
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
- [ ] The workflow then creates the GitHub Release: title `vX.Y.Z`, notes from that version's `CHANGELOG.md` section, the wheel and sdist attached. Do not create it by hand. If the release already exists, the job keeps its title and notes, attaches the files and makes sure it is not a draft.

## Verify

- [ ] Install from PyPI in a clean venv, from a checkout of the tag:

  ```bash
  dir=$(mktemp -d)
  uv venv "$dir/venv"
  uv pip install --no-config --default-index https://pypi.org/simple/ --no-cache --python "$dir/venv" django-observe-kit==X.Y.Z
  "$dir/venv/bin/python" -I scripts/smoke_wheel.py
  ```

- [ ] Check the project page on PyPI: description, links, and files (one wheel, one sdist).
- [ ] Check the GitHub Release: notes and both files.
- [ ] After the first release only: add the PyPI badges to the README (before it, they render as "not found"):

  ```markdown
  [![PyPI](https://img.shields.io/pypi/v/django-observe-kit.svg)](https://pypi.org/project/django-observe-kit/)
  [![Python versions](https://img.shields.io/pypi/pyversions/django-observe-kit.svg)](https://pypi.org/project/django-observe-kit/)
  ```

  They can replace the static Python badge. The PyPI project page shows the new README only from the next release.

## If something goes wrong

- **The upload succeeded but the GitHub Release job failed:** use "Re-run failed jobs", which reuses the files that were uploaded. Do not use "Re-run all jobs": it rebuilds and tries to upload the same version again, which PyPI rejects.
- **The workflow fails before the upload:** nothing was published. Fix it on `main`. If the fix needs a new commit, delete the unpublished tag (using your ruleset bypass) and tag the new commit. First confirm on PyPI that nothing was uploaded.
- **A published release is broken:** yank it on PyPI (project > Manage > Releases > Options > Yank) and give a reason. A yanked version is skipped by installers unless it is pinned exactly. Then fix on `main`, bump the patch version and release again. Do not move or reuse the published tag, and note the yank in the changelog and the GitHub release.
