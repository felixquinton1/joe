# Release guide

This document prepares a release; it does not authorize making the repository
public or publishing packages.

## Before the first public release

1. Add sanitized screenshots under `docs/assets/` using the documented names.
2. Review the complete Git history for credentials, private conversations,
   company names, internal paths, and restricted data.
3. Confirm the repository description, topics, social preview, Discussions,
   issue tracker, and private vulnerability reporting settings on GitHub.
4. Confirm that `joe-orchestrator` is available on PyPI and reserve it through
   the first TestPyPI/PyPI publication.
5. Configure GitHub environments named `testpypi` and `pypi`; require manual
   approval for `pypi`.
6. Add this repository as a trusted publisher on TestPyPI and PyPI, targeting
   `.github/workflows/release.yml` and the matching environment.

## Validate a release candidate locally

```bash
python -m pytest -q
node --test tests/test_web_assets.mjs

cd vscode-extension
npm ci
npm run check
npm test
cd ..

python -m build
python -m twine check dist/*
python -m venv .release-venv
.release-venv/bin/python -m pip install dist/*.whl
.release-venv/bin/joe --version
.release-venv/bin/joe --help
```

On Windows, use `.release-venv\\Scripts\\python.exe` for the final commands.
Delete the local environment after validation.

Inspect both archives before publishing:

```bash
python -m zipfile -l dist/*.whl
tar -tzf dist/*.tar.gz
```

## Publish TestPyPI

Run the **Release packages** workflow manually with target `testpypi`. Install
the result in a new environment using TestPyPI plus PyPI for dependencies:

```bash
pipx install --index-url https://test.pypi.org/simple/ \
  --pip-args='--extra-index-url https://pypi.org/simple/' joe-orchestrator
joe doctor
```

## Publish the public release

1. Ensure `CHANGELOG.md`, `pyproject.toml`, `src/joe/__init__.py`, and the Web
   application version agree.
2. Commit and push the final release candidate.
3. Create and push an annotated tag, for example `v1.1.1`.
4. Run **Release packages** manually from that tag with target `pypi`.
5. Approve the protected `pypi` environment after reviewing the build.
6. Verify PyPI installation on a clean Linux, macOS, or Windows environment.
7. Create the GitHub release from the same tag and attach the wheel, source
   archive, and VSIX if the extension is part of that release.

PyPI versions are immutable. If any check fails after publication, bump the
version and publish a new release instead of replacing files.
