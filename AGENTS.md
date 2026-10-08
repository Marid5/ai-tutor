# AGENTS.md

Instructions for coding agents (Claude Code, Codex and others) working in this repository.

## What this repository is

AI Tutor is a spaced-repetition app whose content is written by a coding agent. A learner hands you their material (notes, photos of a textbook, a PDF, pasted text), you turn it into cards in `content/` following a strict contract, the validator checks every card, and the app teaches them with exercises and an FSRS schedule.

Most requests here are content work: add a lesson, extend a chapter, choose which exercises a chapter uses. The rest is the app itself: a FastAPI + SQLite backend in `app/` and a React client in `frontend/`. For content work, follow the matching skill below even if your tool does not load skills automatically: open the file and do what it says.

## Repository map

| Path | What is there |
|---|---|
| `content/program.yaml` | Course settings: title, language, exercise defaults, schedule, chapter order |
| `content/chapters/<id>.yaml` | One chapter per file: lessons and their cards |
| `docs/content-contract.md` | Every content field and rule, and what each kind of edit does to learner progress |
| `docs/exercises.md` | Exercise kinds, when a card supports each, how a lesson uses them |
| `scripts/validate_content.py` | The validator behind `make validate` |
| `app/` | Backend: content loader (`content.py`), learning engine (`steps.py`, `session.py`, `review.py`), HTTP API (`api/`), user-management CLI (`cli.py`) |
| `migrations/` | SQLite schema migrations, applied in file-name order |
| `frontend/` | React + Vite + TypeScript client (`src/screens`, `src/components`) and Playwright tests (`e2e/`) |
| `tests/` | Backend tests (pytest) |
| `deploy/` | `setup-server.md` (server from scratch), `remote-deploy.sh`, `Caddyfile.example` |
| `.claude/skills/` | Step-by-step workflows for agents (listed below) |
| `.github/workflows/` | CI (`ci.yml`) and deployment after green CI on `main` (`deploy.yml`) |

## Commands

| Command | What it does |
|---|---|
| `make setup` | Creates `.venv`, installs Python and client dependencies, copies `.env.example` to `.env`, creates `data/` |
| `make dev` | Backend on `127.0.0.1:8000` plus the client on `http://localhost:5173`; the backend restarts when `app/` or any YAML file in `content/` changes |
| `make validate` | Checks `content/` against the contract and prints the exercise coverage table |
| `make test` | Backend tests |
| `make lint` | `ruff check` and `ruff format --check` |
| `make format` | Formats Python code |
| `make e2e` | Browser tests against a real backend |
| `make user NAME=ada` | Creates an account (asks for the password; add `ARGS=--password-stdin` to pipe it). Registration is closed by default |
| `make build` | Builds the client into `static/` |
| `make serve` | Production-like run on `http://127.0.0.1:8000` |

Client checks: `npm --prefix frontend run typecheck` and `npm --prefix frontend test`.

For content work only the validator is needed. If `make setup` cannot finish (for example, npm has no network), a Python 3.12 environment is enough for `make validate` and `make test`: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`.

## Rules

- **Content follows the contract** in [docs/content-contract.md](docs/content-contract.md). Do not invent fields: unknown fields are errors.
- **Run `make validate` before every commit that touches `content/`.** It must exit 0. Read its warnings and fix the ones your change caused.
- **Never change, reuse or delete an existing id** (chapter, lesson or card). Progress is stored against ids; a rename wipes it for every learner.
- **Do not edit an existing card's `answer`, `option`, `distractors`, `accepted` or `key_mode` unless asked.** That resets the card's schedule for every learner. Wording fields (`prompt`, `prompt_variants`, `hint`, `note`, `tags`, `source`) are safe to edit.
- **Never edit an applied migration.** Schema changes go in a new file `migrations/NNN_<name>.sql` with the next number.
- **Content changes take effect on restart.** `make dev` restarts the backend by itself; `make serve` needs a restart; production picks them up with the next deploy.
- **Code changes:** `make test` and `make lint` must pass; for changes in `frontend/`, also the client checks above.
- **Never weaken the validator, the content rules or their tests** to make content pass. Fix the content.
- Code, comments and UI are in English. Card text is in the language the learner studies in, and `language` in `program.yaml` names it.
- `tests/test_demo_content.py` checks that whatever is in `content/` loads without errors or warnings; its checks on the shape of the bundled demo course ("How LLMs work") skip themselves once the course is a different one. Content changes never require editing tests.

## Skills

| Skill | Use it to |
|---|---|
| [`.claude/skills/add-content/SKILL.md`](.claude/skills/add-content/SKILL.md) | Turn material (photos, PDF, text, notes) into validated cards and lessons |
| [`.claude/skills/choose-exercises/SKILL.md`](.claude/skills/choose-exercises/SKILL.md) | Decide which exercise kinds a program or chapter enables |
| [`.claude/skills/deploy/SKILL.md`](.claude/skills/deploy/SKILL.md) | Ship `main` to the server and verify the deploy |
| [`.claude/skills/setup-server/SKILL.md`](.claude/skills/setup-server/SKILL.md) | Prepare a fresh server for AI Tutor |

## See also

- [docs/exercises.md](docs/exercises.md): exercise kinds, rungs and the learning ladder
- [docs/architecture.md](docs/architecture.md): how the backend, engine and client fit together
- [docs/deploy.md](docs/deploy.md): Docker, CI and deployment
- [deploy/setup-server.md](deploy/setup-server.md): a server from scratch
