# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue, pull request or discussion for a security problem.

Include what is affected, how to reproduce it and the impact you see. You will get an acknowledgement within a week; a fix and a coordinated disclosure follow as soon as one is ready, with credit if you want it.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |
| < 0.1 | no |

## Scope

In scope: the code in this repository, which is the backend in `app/`, the web client in `frontend/`, the Docker and Compose files, `deploy/` and the GitHub workflows, used as documented.

Out of scope:

- Deployments that change the documented setup: the app port published beyond `127.0.0.1`, `TRUST_PROXY=true` without a reverse proxy in front, `COOKIE_SECURE=false` on a public server, or registration opened deliberately.
- Course content: cards are authored by the owner of an instance and shown to the learners of that instance.
- A learner reading the expected answer from the step payload in their own browser. Grading is server-side and AI Tutor is a self-study tool; see [docs/architecture.md](docs/architecture.md#design-decisions).
- Vulnerabilities in dependencies with no impact on AI Tutor as used here; report those upstream.

## Security measures

- **Accounts.** Passwords are hashed with bcrypt and must be 10 to 72 bytes; longer input is rejected rather than truncated. Unknown usernames cost the same bcrypt work as wrong passwords.
- **Sessions.** A random 256-bit token in an `HttpOnly`, `SameSite=Lax` cookie, `Secure` by default; the database stores only its SHA-256. Sessions expire after 30 days and expired ones are purged. Changing a password signs out every session; `reset-password` and `delete-user` in the CLI do the same.
- **Closed registration.** `REGISTRATION=closed` is the default. Accounts are created with the CLI; there is no HTTP admin interface.
- **Rate limits.** Sign-in is limited per client address (all attempts) and per username (failed attempts only, so a stranger cannot lock out a known learner for long); password changes and registration are limited too. Counters live in SQLite and survive restarts. Behind a proxy, `X-Forwarded-For` is believed only from loopback or private-network peers, and only its right-most entry.
- **Static file containment.** Requested paths are resolved and must stay inside the client build directory; anything else gets the start page. A regression test covers encoded traversal.
- **Response headers.** Every response, errors included, carries `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `X-Frame-Options: DENY` and a `Permissions-Policy` that disables camera, microphone and geolocation. The client has no inline scripts or styles. HSTS is set at the proxy (`deploy/Caddyfile.example`).
- **Input limits.** API request bodies are capped at 256 KB, answer batches at 100 events, and every field is validated; client-supplied text reaches the logs only as a bounded quote.
- **Server-side grading.** The server recomputes every verdict; a client cannot mark its own answer correct or mint extra checks.
- **Network exposure.** Docker publishes the app on `127.0.0.1` only. The server guide adds a firewall that also covers Docker's own rules, and the post-deploy checks verify that the app port is closed from outside.
- **Container and deploy.** The image runs as an unprivileged user. CI deploys with an SSH key restricted to one forced command, the server pulls the repository with a read-only deploy key, and every deploy over a running app backs up the database first. `.env` files, databases and `.git` never enter the image.
- **Supply chain.** Dependencies are pinned to exact versions, Dependabot proposes updates, and CI runs gitleaks over the full history.
