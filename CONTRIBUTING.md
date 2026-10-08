# Contributing

Thanks for helping. Bug reports, fixes and focused improvements are welcome; for a larger change, open an issue first so we can agree on the approach. Security problems go through [SECURITY.md](SECURITY.md), not public issues.

## Setup

Requires Python 3.12, Node.js 22 and `make`. `make setup` looks for `python3.12` on your `PATH`; point it elsewhere with `make setup PYTHON=/path/to/python3.12`.

```bash
make setup             # .venv, Python and client dependencies, .env, data/
make user NAME=you     # an account for local testing
make dev               # backend with reload + client on http://localhost:5173
```

Browser tests need Playwright's Chromium once: `(cd frontend && npx playwright install chromium)`.

## Checks

```bash
make test lint validate e2e
npm --prefix frontend run typecheck
npm --prefix frontend test
```

- `make test`: backend tests (pytest). `make lint`: `ruff check` and `ruff format --check` (`make format` fixes formatting).
- `make validate`: the content validator. `make e2e`: Playwright against a real backend on a throwaway database.
- `typecheck` runs `tsc --noEmit` for the app, its tooling and the browser tests; `npm test` runs Vitest.

CI runs all of these, plus a Docker build with a health check and gitleaks. Add or update tests with every behaviour change.

## Style

- Python: formatted and linted by ruff (settings in `pyproject.toml`), type hints on public functions.
- TypeScript: strict `tsc`, no `any` without a reason; screens in `src/screens/`, reusable pieces in `src/components/`.
- English everywhere: code, comments, UI and docs. Comments say what the code does and why, in product terms.
- Dependencies are pinned to exact versions (`==` in `requirements*.txt`, exact versions and `package-lock.json` for npm).
- Never edit an applied migration; add `migrations/NNN_<name>.sql` instead. Content follows [docs/content-contract.md](docs/content-contract.md).

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `build:`, `refactor:`, `chore:`, and `content:` for course content. Keep the subject short and imperative.

## Pull request checklist

- [ ] One topic per pull request, with a description of what changed and why.
- [ ] `make test lint validate` pass; for client changes also `typecheck`, `npm test` and `make e2e`.
- [ ] Tests cover the change; screenshots for visible UI changes (`SHOTS=1 make e2e` refreshes `docs/screenshots/`).
- [ ] Docs updated where behaviour, commands or settings changed; an entry under a new version or `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
- [ ] No secrets, `.env` files, databases or personal data in the diff.
