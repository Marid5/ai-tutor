# Set up a server from scratch

This guide takes a fresh Ubuntu VPS (22.04 or 24.04) to a running AI Tutor with HTTPS and automatic deploys from GitHub Actions. Replace `tutor.example.com` with your domain and `203.0.113.10` with the server's public address. Point the domain's DNS `A` record at the server before step 6.

The result: only ports 22, 80 and 443 answer from the internet. The app listens on `127.0.0.1:8000` and is reached through Caddy, which terminates TLS, adds HSTS and keeps an access log.

Do the steps as `root` (or with `sudo`) unless stated otherwise.

## 1. Install Docker

Use the official repository:

```bash
apt-get update
apt-get install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker compose version
```

## 2. Create the `deploy` user

```bash
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
install -d -o deploy -g deploy -m 0700 /home/deploy/.ssh
```

> **Warning:** membership in the `docker` group is equivalent to root on this machine: anyone who can run `docker` as `deploy` can mount the whole filesystem into a container. Keep this user for deploys only, never share its login, and restrict the CI key to one command (step 9).

## 3. Firewall

Two layers: `ufw` for the host, and a `DOCKER-USER` block, because Docker writes its own iptables rules ahead of `ufw` and a published container port is reachable from the internet even with `ufw` on. The block is the safety net in case someone publishes a port on `0.0.0.0` by mistake.

Install `ufw` if the image does not have it, and find the public network interface (often `eth0`, but it may be `ens3`, `enp1s0`, ...):

```bash
apt-get install -y ufw
IFACE="$(ip -o route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}')"
echo "Public interface: $IFACE"
```

**Before changing anything, arm an automatic rollback.** A wrong firewall rule can lock you out of SSH; this timer turns the firewall off after five minutes unless you cancel it:

```bash
systemd-run --on-active=300 --unit=firewall-rollback \
  /bin/sh -c 'ufw --force disable; iptables -F DOCKER-USER || true'
```

Keep your current SSH session open. Set the `ufw` rules (SSH first):

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
# Containers talking to services on the host (for example the proxy to the app).
ufw allow from 172.16.0.0/12
```

Back up `/etc/ufw/after.rules`, then append the `DOCKER-USER` block at the end of the file. Traffic from the internet to containers may only reach ports 80 and 443; replies and everything the containers send out are untouched (those packets do not enter through the public interface). The heredoc is unquoted on purpose, so `$IFACE` is filled in with the interface found above:

```bash
cp /etc/ufw/after.rules /etc/ufw/after.rules.bak
cat >> /etc/ufw/after.rules <<RULES

# BEGIN docker-user
*filter
:DOCKER-USER - [0:0]
-F DOCKER-USER
-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A DOCKER-USER -i $IFACE -p tcp -m conntrack --ctorigdstport 80 -j RETURN
-A DOCKER-USER -i $IFACE -p tcp -m conntrack --ctorigdstport 443 -j RETURN
-A DOCKER-USER -i $IFACE -p udp -m conntrack --ctorigdstport 443 -j RETURN
-A DOCKER-USER -i $IFACE -j DROP
-A DOCKER-USER -j RETURN
COMMIT
# END docker-user
RULES
```

This block covers IPv4, which is how Docker publishes ports by default. If you have enabled IPv6 for Docker (`"ip6tables": true` in `/etc/docker/daemon.json`), add the same block without the `-F` line to `/etc/ufw/after6.rules`, with `ip6tables` semantics; if you have not, leave it alone. Either way, never publish an app port without an explicit address (see `docker-compose.yml`).

Enable the firewall:

```bash
ufw --force enable
systemctl restart docker   # re-creates Docker's chains so the DOCKER-USER jump is in place
ufw status verbose
iptables -S DOCKER-USER
```

`iptables -S DOCKER-USER` must show the `-i <your interface> -j DROP` line. If it does not, the block was not loaded: fix it before going on.

**Now confirm you can still get in, and only then cancel the rollback.** The timer is still running (you have five minutes from the moment you armed it). Open a **second** terminal and log in over SSH again with a fresh connection. As soon as that works:

```bash
systemctl stop firewall-rollback.timer
```

If the new login fails, do nothing: within five minutes the timer disables the firewall and you can get back in. To undo the firewall for good, run `ufw --force disable && iptables -F DOCKER-USER` and delete the `docker-user` block from `/etc/ufw/after.rules`.

Keep that second session open while you continue; step 7 checks the domain and the closed port, and a working session is your way back if something turns out wrong. The real proof that the firewall does its job is the external port check in step 7, not the rule listing.

## 4. Get the code

Create the application directory and a **read-only deploy key** so the server can `git clone` without write access to your repository:

```bash
install -d -o deploy -g deploy /srv/ai-tutor
sudo -u deploy ssh-keygen -t ed25519 -N "" -C "ai-tutor server read-only" -f /home/deploy/.ssh/repo_deploy_key
cat /home/deploy/.ssh/repo_deploy_key.pub
```

In your GitHub repository, open *Settings → Deploy keys → Add deploy key*, paste the public key and leave *Allow write access* **unchecked**.

Trust GitHub's host key only after comparing it with the fingerprints GitHub publishes (see "GitHub's SSH key fingerprints" in the GitHub documentation, docs.github.com):

```bash
ssh-keyscan -t ed25519 github.com > /tmp/github_host_key
ssh-keygen -lf /tmp/github_host_key
```

If the printed `SHA256:...` value equals the published ed25519 fingerprint, install it and clone, using the deploy key:

```bash
sudo -u deploy sh -c 'cat >> /home/deploy/.ssh/known_hosts' < /tmp/github_host_key
rm /tmp/github_host_key
sudo -u deploy GIT_SSH_COMMAND="ssh -i /home/deploy/.ssh/repo_deploy_key -o IdentitiesOnly=yes" \
  git clone git@github.com:YOUR-ACCOUNT/ai-tutor.git /srv/ai-tutor
sudo -u deploy git -C /srv/ai-tutor config core.sshCommand \
  "ssh -i /home/deploy/.ssh/repo_deploy_key -o IdentitiesOnly=yes"
```

The last command makes later `git fetch` calls (by `deploy/remote-deploy.sh`) use the same key.

## 5. Configure and start the app

```bash
cd /srv/ai-tutor
sudo -u deploy cp .env.example .env
sudo -u deploy mkdir -p data
sudo chown 10001:10001 data
```

The container runs as user `10001`, so the data folder must belong to it; otherwise the database cannot be created.

Review `.env`. The defaults suit this setup: registration closed, secure cookies, `TRUST_PROXY=true` (the proxy is the only way in). Then start the app and create your account (registration is closed, so this is how users are made):

```bash
sudo -u deploy docker compose up -d --build
sudo -u deploy docker compose exec app python -m app.cli create-user your-name
```

If Caddy runs in a container (variant B in `deploy/Caddyfile.example`), also add `COMPOSE_FILE=docker-compose.yml:docker-compose.edge.yml` to `.env`, so the app additionally listens on the Docker bridge address `172.17.0.1`, and start it again.

## 6. Reverse proxy with HTTPS

Install Caddy on the host from its apt repository (see the Caddy documentation for the current commands), or run it in a container. Copy `deploy/Caddyfile.example` to `/etc/caddy/Caddyfile`, choose variant A (host) or B (container), and put your domain in. Create the log folder and reload:

```bash
install -d -o caddy -g caddy /var/log/caddy
systemctl reload caddy
```

Caddy requests a certificate on the first visit. Open `https://tutor.example.com` and sign in.

## 7. Check the setup

Run these after the first deploy and after every deploy that touches the network or proxy configuration. Keep a second SSH session open while you do.

1. **Health through the domain**, with the commit that is running:
   ```bash
   curl -fsS https://tutor.example.com/api/health
   ```
   The answer is `"status":"ok"` and `git_sha` equals `git -C /srv/ai-tutor rev-parse HEAD`.
2. **The app port is closed from outside.** From your own computer, not from the server:
   ```bash
   curl -m 5 http://203.0.113.10:8000/ ; echo "exit code: $?"
   ```
   It must not answer (a timeout or connection refused, a non-zero exit code).
3. **File serving is safe.** Request a harmless file through a traversal path:
   ```bash
   curl -s https://tutor.example.com/%2e%2e/VERSION
   ```
   The answer must be the app's start page (HTML), never the contents of `VERSION`.
4. **Nothing is published on every interface.** On the server:
   ```bash
   docker ps --format '{{.Names}}\t{{.Ports}}'
   ```
   No line may contain `0.0.0.0:` or `[::]:` (except the proxy's own 80/443 if Caddy runs in a container).
5. Optionally look at the access log: `tail /var/log/caddy/ai-tutor-access.log`.

## 8. Backups

Every deploy takes a backup before replacing a running app (`deploy/remote-deploy.sh`); copies are kept in `data/backups/`. Take one by hand at any time:

```bash
docker compose exec -T app python -m app.cli backup
```

Copy `data/backups/` off the server regularly: a backup on the same disk does not survive losing the disk.

## 9. Automatic deploys from GitHub Actions

On every push to `main`, once CI has passed, `.github/workflows/deploy.yml` logs in to the server and runs `deploy/remote-deploy.sh`.

On your own computer, create a dedicated key pair for this. It produces `ai_tutor_actions_key` (private, goes to GitHub) and `ai_tutor_actions_key.pub` (public, goes to the server):

```bash
ssh-keygen -t ed25519 -N "" -C "ai-tutor actions deploy" -f ./ai_tutor_actions_key
cat ai_tutor_actions_key.pub
```

On the server, authorise the public key for `deploy` with a **forced command**: whatever the client asks for, this key can only run the deploy script, with no terminal and no port forwarding. Put the single line printed above in place of `PASTE-PUBLIC-KEY-HERE`:

```bash
sudo -u deploy sh -c 'echo "command=\"/srv/ai-tutor/deploy/remote-deploy.sh\",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding PASTE-PUBLIC-KEY-HERE" >> /home/deploy/.ssh/authorized_keys'
sudo -u deploy chmod 600 /home/deploy/.ssh/authorized_keys
```

Collect the server's host key so the workflow can verify it instead of trusting the first connection blindly. Use exactly the string you will store as `DEPLOY_HOST` (known_hosts matches by that name), and compare the fingerprint with the one the server shows (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`):

```bash
ssh-keyscan -t ed25519 203.0.113.10
```

In the GitHub repository, open *Settings → Secrets and variables → Actions* and add these repository secrets:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | the server's address or host name, e.g. `203.0.113.10` |
| `DEPLOY_SSH_KEY` | the **private** key file `ai_tutor_actions_key` (whole file) |
| `DEPLOY_KNOWN_HOSTS` | the output of `ssh-keyscan` above |

Delete the local copies of the key afterwards. Until `DEPLOY_HOST` is set, the workflow does nothing and says so.

To deploy by hand from the server: `sudo -u deploy /srv/ai-tutor/deploy/remote-deploy.sh`.

## Updating the server's setup

Keep deployment configuration in git: change `docker-compose.yml`, the Caddy block and the scripts in the repository and let a deploy bring them to the server. If you must fix something on the server directly, copy the change into the repository the same day, otherwise the next deploy overwrites it (`git reset --hard`).
