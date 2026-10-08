# Deploy

AI Tutor runs as one Docker container: the backend, the built web client and the course content in one image, with the SQLite database on a mounted volume (`./data`). The container publishes port 8000 on `127.0.0.1` only; a reverse proxy with TLS in front of it is the public entry point.

| File | Role |
|---|---|
| `Dockerfile` | Two stages: builds the client with Node 22, then a Python 3.12 slim image running as an unprivileged user (uid `10001`). Validates the content during the build and has a health check. |
| `docker-compose.yml` | The `app` service: `127.0.0.1:8000:8000`, `env_file: .env`, `./data` mounted at `/app/data`. |
| `docker-compose.edge.yml` | Override for a reverse proxy that runs in its own container: also binds the Docker bridge address `172.17.0.1:8000`. |
| `deploy/Caddyfile.example` | Caddy with automatic TLS, HSTS and a rotated access log; variant A (Caddy on the host) and B (Caddy in a container). |
| `deploy/setup-server.md` | A fresh Ubuntu server to a running, firewalled, HTTPS deployment. |
| `deploy/remote-deploy.sh` | What a deploy runs on the server. |
| `.github/workflows/deploy.yml` | Runs `remote-deploy.sh` over SSH after CI passes on `main`. |

## Local Docker

```bash
cp .env.example .env
mkdir -p data
sudo chown 10001:10001 data     # Linux: the container user must own the data folder
docker compose up -d --build
docker compose exec app python -m app.cli create-user you
```

Open http://127.0.0.1:8000 and sign in. `.env.example` is written for a server behind a proxy. For a local run over plain HTTP without a proxy, set `COOKIE_SECURE=false` (some browsers drop a `Secure` cookie on `http://`) and `TRUST_PROXY=false` in `.env`, then `docker compose up -d` again.

Useful commands:

```bash
docker compose ps                                   # state and health
docker compose logs -f app                          # application log
curl -s http://127.0.0.1:8000/api/health            # version, git_sha, program_version, counts
docker compose exec app python -m app.cli list-users
docker compose exec app python -m app.cli reset-password you
docker compose exec app python -m app.cli delete-user you --yes
docker compose down                                 # stop; data/ stays
```

Content is baked into the image, so after editing `content/` rebuild: `docker compose up -d --build`.

## Production on a VPS

[deploy/setup-server.md](../deploy/setup-server.md) is the full procedure; the `setup-server` skill walks an agent through it. In short:

1. Install Docker from the official repository and create a `deploy` user for deployments only (membership in the `docker` group is equivalent to root).
2. Firewall: `ufw` allows 22, 80 and 443, and a `DOCKER-USER` block drops anything else that reaches a container from the public interface, armed with an automatic rollback timer so a wrong rule cannot lock you out.
3. Clone the repository to `/srv/ai-tutor` with a read-only deploy key, create `.env` from `.env.example` (registration closed, secure cookies, `TRUST_PROXY=true`), give `data/` to uid `10001`, start the app and create your account with the user-management CLI.
4. Put Caddy in front, on the host or in a container, from `deploy/Caddyfile.example`. It obtains the certificate and adds HSTS.
5. Run the post-deploy checks below, then set up automatic deploys.

## Automatic deploys from GitHub Actions

`.github/workflows/deploy.yml` starts when the CI workflow finishes successfully for a push to `main`. It connects as `deploy` with a key that the server restricts to one forced command, `deploy/remote-deploy.sh`, which:

1. takes a lock, so two deploys never overlap;
2. fetches `origin/main` and resets the checkout to it;
3. backs up the database if the app is running (the first deploy has nothing to back up); a failed backup stops the deploy;
4. rebuilds and restarts the container with `GIT_SHA` set to the commit;
5. waits up to 60 seconds for `/api/health` to report that commit, and otherwise exits non-zero with the last 50 lines of the app log.

Repository secrets (*Settings → Secrets and variables → Actions*):

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | Server address or host name |
| `DEPLOY_SSH_KEY` | The private key whose public half is the forced-command key on the server |
| `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan` output for exactly the `DEPLOY_HOST` value, checked against the server's fingerprint |

While `DEPLOY_HOST` or `DEPLOY_SSH_KEY` is unset, the workflow is skipped: the run is green and its log says "Deployment is not configured". A fork or a template copy without a server therefore never fails on deploy. `DEPLOY_KNOWN_HOSTS` is not optional once the other two are set: without it the SSH connection fails host key verification. How to create the key and the host entry: [setup-server.md, step 9](../deploy/setup-server.md#9-automatic-deploys-from-github-actions).

To deploy by hand on the server: `sudo -u deploy /srv/ai-tutor/deploy/remote-deploy.sh`.

## Post-deploy checks

Run these after the first deploy and after every deploy that touches the network or the proxy (the `deploy` skill runs them every time). Replace the domain and address with yours.

| Check | Command | Expected |
|---|---|---|
| The new commit is serving | `curl -fsS https://tutor.example.com/api/health` | `"status":"ok"` and `git_sha` equal to the deployed commit |
| The app port is closed from outside (run it from another machine) | `curl -m 5 http://203.0.113.10:8000/` | No answer: timeout or connection refused |
| File serving stays inside the client build | `curl -s https://tutor.example.com/%2e%2e/VERSION` | The HTML start page, never the version string |
| Nothing is published on every interface (on the server) | `docker ps --format '{{.Names}}\t{{.Ports}}'` | No `0.0.0.0:` or `[::]:` except a containerised proxy's 80/443 |

A failure in any check but the first is a security problem: stop deploying until it is fixed. Details: [setup-server.md, step 7](../deploy/setup-server.md#7-check-the-setup).

## Backups

```bash
docker compose exec -T app python -m app.cli backup              # writes data/backups/ai_tutor-YYYYMMDD-HHMMSS.db
docker compose exec -T app python -m app.cli backup --keep 30    # keep the newest 30 instead of 14
```

`app.cli backup` uses SQLite's online backup API, so it is consistent while the app keeps serving. Files are owner-only (they contain password hashes) and the command rotates only the files it wrote. Every automatic deploy takes one first. Copy `data/backups/` off the server on a schedule of your own: a backup on the same disk does not survive losing the disk.

To restore, on the server in `/srv/ai-tutor`:

1. `docker compose stop app`
2. Move `data/ai_tutor.db`, `data/ai_tutor.db-wal` and `data/ai_tutor.db-shm` aside.
3. Copy the chosen backup to `data/ai_tutor.db`, owned by uid `10001` with mode `600`.
4. `docker compose up -d`

## Upgrades

- **Your course:** content changes reach the server with the next deploy. What each kind of edit does to learner progress is in [content-contract.md](content-contract.md#lifecycle-what-happens-when-content-changes).
- **The app:** read [CHANGELOG.md](../CHANGELOG.md), bring the changes into your repository (for a template copy, add this repository as a remote and merge its `main`), run `make test validate`, and push. Schema migrations in `migrations/` apply automatically when the new container starts, after the pre-deploy backup.
- **Dependencies** are pinned to exact versions; Dependabot (`.github/dependabot.yml`) proposes weekly updates for pip, npm and GitHub Actions, and CI must pass before they merge.
- **Rolling back:** `git revert` the change and push; the deploy brings back the previous version. A migration is not reversed by this, so restore the pre-deploy backup if a schema change has to go.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Container keeps restarting; the log shows `unable to open database file` | `data/` is not owned by uid `10001`: `sudo chown 10001:10001 data`. |
| Container exits at start with a list of content errors | The content is invalid; run `make validate` locally and fix it. The image build normally catches this first. |
| Sign-in succeeds but the next request is signed out | `COOKIE_SECURE=true` over plain HTTP. Use HTTPS, or `COOKIE_SECURE=false` for local use only. |
| Every visitor hits "too many attempts" together | `TRUST_PROXY` is off behind a proxy, so everyone shares the proxy's address. Set `TRUST_PROXY=true`. |
| The deploy run is green but nothing changed | Its log says "Deployment is not configured": set the secrets above. |
| The deploy fails the health check | The new commit did not report healthy within 60 seconds. The workflow log shows the app log; fix forward or revert. |
