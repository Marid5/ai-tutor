---
name: setup-server
description: Guides the user through preparing a fresh Ubuntu VPS to host AI Tutor (Docker, a deploy user, firewall, Caddy with HTTPS, read-only deploy key, first account, GitHub Actions secrets) by following deploy/setup-server.md, and verifies the result. Use when the user wants to host AI Tutor on their own server for the first time, or to rebuild or audit an existing server.
---

# Set up a server

The procedure is `deploy/setup-server.md`. Follow it step by step and do not reinvent or shorten it: every command there is part of the security baseline. This skill adds how to run it safely as an agent and how to know each step is done. `docs/deploy.md` explains the moving parts.

## Before you start

Collect from the user:

- the server's public IP address and SSH access as `root` or a sudo user (Ubuntu 22.04 or 24.04);
- the domain, with its DNS `A` record pointing at the server (needed before step 6);
- the GitHub repository (`owner/name`) the server will deploy from;
- the username for the first account in the app;
- whether Caddy runs on the host (variant A, recommended) or in a container (variant B).

**Who runs the commands.** If you have a shell on the server, run each step's commands yourself, one step at a time, and show the output. Otherwise give the user one step at a time and wait for its output before the next. Either way, never ask for passwords or private keys in the chat: the user types the account password into the CLI prompt, and private keys go straight into GitHub secrets.

## Steps and their "done" checks

| Step in `deploy/setup-server.md` | Done when |
|---|---|
| 1. Install Docker | `docker compose version` prints a version. |
| 2. Create the `deploy` user | `id deploy` lists the `docker` group. Tell the user plainly: **membership in `docker` is equivalent to root**, so this account is for deploys only. |
| 3. Firewall | `ufw status verbose` allows 22/tcp, 80/tcp, 443/tcp, 443/udp and `172.16.0.0/12` (containers to host), nothing else; `iptables -S DOCKER-USER` shows the `-i <interface> -j DROP` line; a **new** SSH login works; only then is the rollback timer stopped. |
| 4. Get the code | The deploy key was added in GitHub with *Allow write access* unchecked; GitHub's host-key fingerprint was compared with the published one; `/srv/ai-tutor` is a clone owned by `deploy`. |
| 5. Configure and start | `.env` exists (registration closed, `COOKIE_SECURE=true`, `TRUST_PROXY=true`); `data/` belongs to `10001`; `docker compose ps` shows `app` running; the first account exists (`docker compose exec app python -m app.cli list-users`). |
| 6. Reverse proxy | `https://<domain>` opens the sign-in page with a valid certificate; the access log folder exists. |
| 7. Check the setup | All four checks below pass. |
| 8. Backups | The user knows where backups are and copies them off the server. |
| 9. GitHub Actions | The forced-command key is in `authorized_keys`; the secrets `DEPLOY_HOST`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` are set; local copies of the private key are deleted; the next push to `main` deploys (see the `deploy` skill). |

### Step 3 needs extra care

A wrong firewall rule locks everyone out. Before any firewall change: arm the rollback timer exactly as the guide shows, and keep the current SSH session open. Confirm with the user that a second, fresh SSH login works **before** stopping the timer. If that login fails, do nothing: the timer turns the firewall off within five minutes.

## Final checks (step 7 of the guide)

Report each result to the user:

1. `curl -fsS https://<domain>/api/health` answers `"status":"ok"`, and `git_sha` equals `git -C /srv/ai-tutor rev-parse HEAD` on the server.
2. From outside the server, `curl -m 5 http://<server-ip>:8000/` fails (timeout or connection refused).
3. `curl -s --path-as-is https://<domain>/%2e%2e/VERSION` returns the app's HTML start page, never the bare version string.
4. On the server, `docker ps --format '{{.Names}}\t{{.Ports}}'` shows no `0.0.0.0:` or `[::]:`, except the proxy's 80 and 443 when Caddy runs in a container.

A failure in 2, 3 or 4 means the server is exposed: stop, tell the user, and fix it before anything else.

## Never

- Never publish the app port without an address (`8000:8000`); keep `127.0.0.1:8000:8000`, plus `docker-compose.edge.yml` only for a proxy in a container.
- Never give the server's deploy key write access to the repository.
- Never turn on `REGISTRATION=open` or `COOKIE_SECURE=false` on a public server unless the user asks for it and understands the consequence.
- Never stop the firewall rollback timer before a fresh SSH login has worked.
- Never change the server by hand without copying the change into the repository the same day; the next deploy resets the checkout.
