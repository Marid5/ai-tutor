# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-07

First public release.

### Added

#### Content

- Content model v1: a course is `program.yaml` plus one YAML file per chapter; chapters hold lessons of 1 to 10 cards, and a card is a prompt with an answer, an optional short `option`, three distractors, accepted keys, a hint, a note, tags and a source.
- Validator (`make validate`) that rejects unknown fields, duplicate YAML keys, duplicate or malformed ids and cards no enabled exercise can check, warns about mismatched option lengths and unused exercise kinds, and prints the exercise coverage per chapter. It runs in CI, in the Docker build and at app start.
- Content lifecycle keyed by stable ids: wording edits keep progress, an edit of a card's answer material resets that card for every learner, removed items are retired with their history and restored if the id returns.
- Demo course "How LLMs work": 33 original cards in three chapters.

#### Learning engine

- Five step kinds: `triage`, `flash`, `choice`, `cloze` and `assemble`, with per-program and per-chapter switches for all but `flash`.
- Server-side grading of closed exercises that ignores case, accents and repeated whitespace.
- Lessons with a primary check per card and a learning ladder after a miss (a flash card, then each rung with one retry).
- FSRS scheduling through py-fsrs 3.x with configurable retention, maximum interval, time zone and day start.
- Scheduled reviews in sittings of up to 10 cards, lesson repeat and mixed practice that holds intervals instead of stretching them.
- Session queues rebuilt from accepted answers, so a reload or a second device continues with the same step; idempotent answer batches; step ids pinned to the card's check version, with stale steps rejected.
- Home board with the next lesson, readiness by chapter, and a progress screen with upcoming reviews for the next seven days that have any.

#### Accounts and security

- Username and password accounts with bcrypt; registration closed by default.
- User-management CLI (`python -m app.cli`): `create-user`, `reset-password`, `list-users`, `delete-user`, and `backup` with rotation.
- Opaque session tokens stored as SHA-256 hashes in `HttpOnly`, `SameSite=Lax`, `Secure` cookies; sessions revoked on password change, reset and deletion.
- Rate limits for sign-in, registration and password change, persisted in SQLite; proxy-aware client address resolution.
- Strict security headers and Content-Security-Policy, request body limits, and static file containment.

#### Web client

- React, Vite and TypeScript client: sign-in, home, session, done, progress and settings screens.
- Mobile-first layout, light, dark and system themes, keyboard shortcuts, visible focus and screen-reader announcements.
- Per-learner setting to show hints by default; password change and sign-out.

#### Agent layer

- `AGENTS.md` and `CLAUDE.md` for coding agents, and four skills: `add-content`, `choose-exercises`, `deploy` and `setup-server`.
- Content contract and exercise guide in `docs/`.

#### Deployment and tooling

- Two-stage Docker image running as an unprivileged user, with a health check and content validation at build time.
- Compose file publishing on `127.0.0.1` only, an override for a containerised reverse proxy, and a Caddy example with TLS, HSTS and access logs.
- Server setup guide with firewall, read-only deploy key and forced-command CI key; deploy script with locking, pre-deploy backup and a health gate on the deployed commit.
- GitHub Actions: CI (ruff, pytest, validator, release version check, tsc, Vitest, Playwright, Docker smoke test, gitleaks) and an optional deploy after green CI; Dependabot.
- `make` targets for setup, development, tests, validation, accounts, builds and a production-like run.

## Planned

- Words with forms and a typed-answer step.
- Distractor suggestions drawn from the same chapter.
- An exam date per course, with intervals tightened to fit before it.
- Telegram reminders and study.
- An HTTP admin interface.
- A multilingual interface.
- Import from Anki.
- A service worker for offline use.
- A setup script that arms and cancels the firewall rollback timer by itself (today a documented manual step).
- Upgrade of py-fsrs beyond 3.x (4.x/5.x).
