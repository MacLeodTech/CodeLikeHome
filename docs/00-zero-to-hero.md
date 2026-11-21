# Zero to hero: from an empty server to VS Code on your tablet

This is the whole journey in order, with a check after each step. Each step links to the detailed page. Allow an evening.
If something fails, stop at the failing check; every step is safe to repeat.

## What you need before you start

| You need | Notes |
|---|---|
| A Linux server with systemd (Debian/Ubuntu/Fedora all fine) | the machine that will hold your code |
| A user account on it that you work as | **not root**. `id` should show a normal uid, usually 1000 |
| Docker Engine and the Compose plugin | <https://docs.docker.com/engine/install/>. Check: `docker compose version` |
| `sudo` rights for that user | for the installer |
| A domain name you control, with DNS you can edit | for example `example.com`; you will use `code.example.com` |
| A way for your tablet to reach the server | **either** a router port-forward for 443 to a reverse proxy, **or** a tunnel (Cloudflare Tunnel, Tailscale Funnel, or similar). A VPN-only setup (WireGuard/Tailscale on the tablet) also works and is the safest |
| A reverse proxy that does TLS | Caddy is used in the examples; Traefik or nginx are fine. It must pass websockets and not buffer |
| An identity provider for SSO (recommended) | Google, GitHub, or a self-hosted one (Authentik, Keycloak, Authelia). Without SSO, Warpgate password + TOTP still works |
| An authenticator app | for the one-time codes |

If you have none of the networking pieces yet, the shortest path is: Docker + Caddy on the server, a Cloudflare Tunnel to Caddy,
Google or GitHub for SSO.

## Step 1: pick a security level (2 minutes)

Decide how much friction you will accept; you can change it later by re-running the installer. Read the table in
[06-profiles.md](06-profiles.md). If unsure, start with **`strict`** (the default). If you want `docker` available in the editor,
choose `balanced --groups docker` and read what that gives up.

## Step 2: install code-server for your user (5 minutes)

```sh
curl -fsSL https://code-server.dev/install.sh | sh -s -- --method=standalone
~/.local/bin/code-server --version
```

Check: the version prints. Do not start it; the installer sets up the service. Detail: [02-setup.md](02-setup.md) step 1.

## Step 3: install the host half (5 minutes)

```sh
git clone https://github.com/OWNER/CodeLikeHome && cd CodeLikeHome
sudo sh host/install.sh --user "$USER" --workspace "$HOME/projects" --dry-run
sudo sh host/install.sh --user "$USER" --workspace "$HOME/projects"      # add --profile ... if you chose one
```

Check: `ls -l ~/.local/run/code-server/` shows a socket `srw-------`, and `ss -tlnp | grep -i code` prints nothing (no TCP port).
Detail: [02-setup.md](02-setup.md) step 2.

## Step 4: start the forwarder (3 minutes)

```sh
cp .env.example .env
$EDITOR .env        # CLH_UID=$(id -u), CLH_SOCKET_DIR=/home/YOU/.local/run/code-server, CLH_ALLOW=warpgate
docker compose -f compose/codelikehome.yaml --env-file .env up -d
docker logs vscode
```

Check: the container is running and `docker network inspect vscode_net` shows it. The `CLH_ALLOW` value is the container name your
bastion will have in the next step.

## Step 5: set up Warpgate (15 minutes)

Warpgate is the front door: SSO, a one-time code, and a record of who opened what.

1. Create its configuration once (interactive; it asks for the database, ports and an admin password):

   ```sh
   mkdir warpgate && cd warpgate
   docker run --rm -it -v "$PWD/warpgate-data:/data" ghcr.io/warp-tech/warpgate setup
   ```

   Accept the defaults unless you know you need otherwise. Note the admin username and password.
2. Copy `examples/warpgate-compose.yaml` here, check that `container_name` equals your `CLH_ALLOW`, and start it:
   `docker compose -f warpgate-compose.yaml up -d`.
3. Open the admin UI (through your proxy, or temporarily `https://localhost:8888` with the port published to `127.0.0.1`), sign in, and
   follow [02-setup.md](02-setup.md) step 4: add the **Web (HTTP)** target `vscode` with URL `http://vscode:8443`, create a role,
   assign it to the target and to you only, add a TOTP credential, and require it.
4. **SSO** (recommended): create an OAuth client at your provider with the redirect URI
   `https://code.example.com/@warpgate/api/sso/return`, then add it under `sso_providers` in `warpgate-data/warpgate.yaml`
   (restart Warpgate after editing). Warpgate's own documentation has the current syntax for Google, GitHub and generic OIDC; follow it
   for the exact keys. Link your Warpgate user to your SSO identity. Keep password login as a break-glass path from your home
   network only (the Caddy example enforces that).
5. In `warpgate.yaml` set `external_host` and `http.external_host`, `http.external_port: 443`, and `trust_x_forwarded_headers: true`
   when it sits behind a proxy; without these, SSO redirects and cookies break.

Check: from inside `docker exec warpgate ...` or a throwaway container on `vscode_net`, the Warpgate container resolves and the
forwarder accepts it: `docker logs vscode` shows `accepted connection from ...` after you open the target in step 7.

## Step 6: DNS, TLS and the front door (10 minutes)

Point `code.example.com` at your reverse proxy (an A record, or the tunnel's CNAME). Add the site from
[`examples/Caddyfile.warpgate`](../examples/Caddyfile.warpgate), replacing the hostname. Reload the proxy. Detail: [02-setup.md](02-setup.md)
step 5 (public access) and the tunnel notes in [03-security.md](03-security.md).

Check: `curl -I https://code.example.com` returns the Warpgate login page, not an error.

## Step 7: sign in and open the editor (2 minutes)

Browse to `https://code.example.com`, sign in (SSO, then the one-time code), choose the `vscode` target. Direct link:
`https://code.example.com/?warpgate-target=vscode`. On Android, install it as an app from the browser menu ("Install app"); see
[04-tablet-guide.md](04-tablet-guide.md).

## Step 8: verify (2 minutes)

```sh
sudo sh host/verify.sh --user "$USER" --container vscode --bastion warpgate --network vscode_net
```

Every line should say `ok` (or `info` for choices your profile made on purpose). The same script runs hourly; to be told when it
fails, put a notification script at `/etc/codelikehome/notify` ([02-setup.md](02-setup.md)).

## When it does not work

| Symptom | Look at |
|---|---|
| Blank page or a loop after SSO login | `external_host` / `trust_x_forwarded_headers` in `warpgate.yaml`; proxy must set `Host` |
| Editor loads, then "Disconnected" repeatedly | the proxy is buffering or a CDN is rewriting scripts: `flush_interval -1`, `no-transform`, disable Rocket Loader |
| `502` / "connection refused" opening the target | forwarder not running, or Warpgate not on `vscode_net`: `docker network inspect vscode_net` |
| `docker logs vscode` says `refused connection from ...` | `CLH_ALLOW` does not match Warpgate's container name |
| Forwarder cannot open the socket | `CLH_UID` is not the editor user's uid, or `CLH_SOCKET_DIR` is wrong |
| `systemctl status code-server` fails | `~/.local/bin/code-server` missing (step 2), or run `journalctl -u code-server` |
| Agent or terminal says docker permission denied, or files are `0600` | that is the `strict` profile working; see [06-profiles.md](06-profiles.md) |
| Tablet-specific issues | [04-tablet-guide.md](04-tablet-guide.md) |

## Keeping it healthy

* **Updates:** [05-updating.md](05-updating.md). Pin the forwarder tag and Warpgate's image; bump them on purpose.
* **Backups:** your code lives in your normal home directory, so your normal backups cover it. Also back up `warpgate-data/`
  (users, roles, TOTP secrets, session recordings) and your `.env`.
* **Uninstall:** `docker compose -f compose/codelikehome.yaml down`; `sudo systemctl disable --now code-server codelikehome-verify.timer`;
  `sudo rm /etc/systemd/system/code-server.service /etc/systemd/system/codelikehome-verify.* /etc/sudoers.d/90-codelikehome-timeout`;
  `sudo rm -r /etc/codelikehome /etc/code-server /usr/local/lib/codelikehome`; `sudo systemctl daemon-reload`.
  Your files are untouched.
