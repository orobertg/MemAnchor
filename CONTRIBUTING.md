# Contributing to MemAnchor

Thanks for wanting to help. MemAnchor is open source and welcomes
contributions of all sizes — from typo fixes to new storage backends.

If you're an AI coding agent working in this repo, see
[AGENTS.md](AGENTS.md) instead — it covers the same ground with more
implementation detail and the design principles a change needs to respect.

## Getting Started

```bash
# Fork the repo on GitHub first, then clone your fork
git clone https://github.com/<your-username>/MemAnchor.git
cd MemAnchor
git remote add upstream https://github.com/orobertg/MemAnchor.git

# Requires Python 3.12 and pipenv (https://pipenv.pypa.io/)
pipenv install --dev
```

See the README's [disk space note](README.md#setup) if `sentence-transformers`
pulling in `torch` is a problem for your default pip cache / `/tmp`.

## Running Tests

```bash
pipenv run pytest tests/ -v
```

All tests must pass before submitting a PR. Each test builds an isolated
app instance against a fresh `tmp_path` SQLite file and Chroma collection
(see `tests/helpers.py`), so tests don't share state or require any
external service or API key.

## Project Structure

See the README's [Repo layout](README.md#repo-layout) for the full
annotated tree.

```
memanchor/     ← core package (contracts, adapters, domain, application, db, endpoints)
tests/         ← test suite, one file per module
tools/         ← calibrate_threshold.py, backup_snapshot.sh (also under memanchor/tools/)
```

## PR Guidelines

1. Fork the repo and create a feature branch: `git checkout -b feat/my-thing`
2. Write your code, following the conventions below
3. Add or update tests — write the failing test first if you're fixing a
   bug or adding behavior; a pure refactor with no behavior change can rely
   on the existing suite instead of a new test
4. Run `pipenv run pytest tests/ -v` — everything must pass
5. Commit with a clear message. This project doesn't mandate a specific
   format, but [conventional commits](https://www.conventionalcommits.org/)
   (`fix:`, `feat:`, `docs:`, `test:`, `refactor:`) are a reasonable default
   if you don't have another preference
6. Push to your fork and open a PR against `main`

## Code Style

- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes
- **Type hints**: throughout — `from __future__ import annotations` at the
  top of every module
- **Comments**: only where the *why* isn't obvious from the code itself (a
  hidden constraint, a subtle invariant, a workaround for a specific bug) —
  don't add comments that just restate what the code does
- **No linter/formatter is configured yet** — match the surrounding file's
  existing formatting rather than reformatting wholesale in an unrelated PR
- **Dependencies**: minimize. Don't add a new runtime dependency without
  discussing it in an issue first

## Where to Start

The README's [Known limitations](README.md#known-limitations) section lists
real, documented gaps that are good starting points — authentication
middleware, a circuit breaker for validator calls, multi-writer concurrency
beyond SQLite's serialization, and value-normalization edge cases (units,
formats) are all genuine opportunities that don't require deep familiarity
with the rest of the system first. Test coverage and documentation
improvements are always welcome too.

If you're planning something larger — a new storage backend, a change to
the contradiction-resolution or cardinality-enforcement logic — please open
an issue first to discuss the approach. See [AGENTS.md](AGENTS.md)'s Design
Principles for the constraints any such change needs to respect.

## Versioning

MemAnchor follows [Semantic Versioning](https://semver.org/)
(`MAJOR.MINOR.PATCH`). `memanchor/version.py`'s `__version__` is the single
source of truth — nothing else needs to be kept in sync with it. Releases
are recorded in [CHANGELOG.md](CHANGELOG.md) following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/): add your change
under `[Unreleased]` in your PR if it's user-facing; a maintainer moves
`[Unreleased]` into a new dated version section and tags the release
(`vMAJOR.MINOR.PATCH`) when cutting one.

## Git Identity for Contributions

Before pushing commits, verify Git is configured with an email address
GitHub can associate with your account:

```bash
git config user.name
git config user.email
```

This matters especially if your commits are made through an agentic coding
tool — those tools don't always inherit your normal shell Git configuration,
and an unresolved or placeholder author email creates avoidable friction
when a PR is reviewed.

## License

MIT — see [LICENSE](LICENSE). Your contributions will be released under the
same license.
