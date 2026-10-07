---
name: deploy
description: Ships the main branch of AI Tutor to the user's server through CI and the deploy workflow, then verifies the running commit and the security checks. Use when the user asks to deploy, release or ship a change, or to check that a deploy worked.
---

# Deploy

A push to `main` runs CI (`.github/workflows/ci.yml`). When CI passes, `.github/workflows/deploy.yml` connects to the server over SSH with a key that can only run `deploy/remote-deploy.sh`. That script fetches `origin/main`, resets the checkout to it, backs up the database if the app is running, rebuilds and restarts the container, and waits up to 60 seconds for `/api/health` to report the new commit. Background and troubleshooting: `docs/deploy.md`. Server details: `deploy/setup-server.md`.

You need: a server prepared with `deploy/setup-server.md` (otherwise use the `setup-server` skill), the repository secrets `DEPLOY_HOST`, `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`, the domain, and the server's public IP address. Ask the user for the domain and the address if you do not know them.

## 1. Check before pushing

1. `git status --short` is clean and the branch is `main` (or the change is merged into `main` through a pull request).
2. `make validate`, `make test` and `make lint` pass.
3. If `frontend/` changed: `npm --prefix frontend run typecheck` and `npm --prefix frontend test` pass.

Do not push red. CI runs the same checks and the deploy would not start anyway.

## 2. Push and follow the workflows

1. `git push origin main`; if the change was merged through a pull request, skip the push. Never force-push `main`.
2. Find the CI run for the commit and wait for it: `gh run list --workflow CI --commit "$(git rev-parse origin/main)" --limit 1`, then `gh run watch <run-id> --exit-status`.
3. Then the deploy: `gh run list --workflow Deploy --commit "$(git rev-parse origin/main)" --limit 1`, then `gh run watch <run-id> --exit-status`.
   - A green run whose log says "Deployment is not configured" deployed nothing: the secrets are missing (see `deploy/setup-server.md`, step 9).
   - On failure, read `gh run view <run-id> --log-failed`. A health-check failure prints the last 50 lines of the app's log.

## 3. Verify the deploy

Run all four checks after every deploy and report each result. Replace `tutor.example.com` and `203.0.113.10` with the user's domain and server address.

1. **The new commit is serving, through the domain.**
   ```bash
   curl -fsS https://tutor.example.com/api/health
   git rev-parse origin/main
   ```
   `status` is `ok` and `git_sha` equals the commit. For content changes, `program_version` must equal the local value: `.venv/bin/python -c "from app.content import load_program; print(load_program('content').program_version)"`.
2. **The app port is closed from outside.** Run it from your machine, not from the server:
   ```bash
   curl -m 5 http://203.0.113.10:8000/ ; echo "exit code: $?"
   ```
   It must fail (timeout or connection refused, non-zero exit code).
3. **File serving does not leave the app's directory.** A harmless traversal probe:
   ```bash
   curl -s --path-as-is https://tutor.example.com/%2e%2e/VERSION | head -c 200
   ```
   The answer must be the app's HTML start page, never the bare version string from `VERSION`.
4. **Nothing is published on every interface.** On the server (needs the user's own SSH login; the CI key cannot open a shell):
   ```bash
   docker ps --format '{{.Names}}\t{{.Ports}}'
   ```
   No line contains `0.0.0.0:` or `[::]:`, except the proxy's own ports 80 and 443 when Caddy runs in a container.

If check 2, 3 or 4 fails, treat it as a security incident: tell the user at once and stop deploying until it is fixed.

## When a deploy fails

- **Health check failed:** the new commit did not report healthy within 60 seconds. Read the log in the workflow output, fix forward with a new commit, or revert: `git revert <sha>` and `git push origin main`, which deploys the previous state.
- **Data looks wrong after the deploy:** every deploy over a running app first writes a backup to `/srv/ai-tutor/data/backups/`. Restoring one replaces the live database, so give the user the steps and let them run them: in `/srv/ai-tutor`, `docker compose stop app`; move `data/ai_tutor.db`, `data/ai_tutor.db-wal` and `data/ai_tutor.db-shm` aside; copy the chosen backup to `data/ai_tutor.db` with owner `10001` and mode `600`; `docker compose up -d`.
- **Manual deploy:** on the server, `sudo -u deploy /srv/ai-tutor/deploy/remote-deploy.sh` does exactly what the workflow does.

## Never

- Never edit files on the server by hand: the next deploy resets the checkout. Change the repository instead.
- Never publish the app port without `127.0.0.1:` (or the bridge address in `docker-compose.edge.yml`).
- Never put secrets, `.env` or keys into the repository or the workflow logs.
- Never skip the checks in step 3 because the workflow is green.
