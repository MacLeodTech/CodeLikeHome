# Setup

Assumes a Linux host with systemd, Docker and Compose, a reverse proxy, a running Warpgate (or another bastion) and an identity
provider for SSO. Replace `YOU` with the editor's user and `example.com` with your domain.

## 1. Install code-server for your user

`curl -fsSL https://code-server.dev/install.sh | sh -s -- --method=standalone` puts it in `~/.local`. Do not start it; the installer
below sets up its service.

## 2. The host half

```sh
git clone https://github.com/OWNER/CodeLikeHome && cd CodeLikeHome
sudo sh host/install.sh --user YOU --workspace /home/YOU/projects --dry-run     # read what it will do
sudo sh host/install.sh --user YOU --workspace /home/YOU/projects
```

Options: `--env none|allowlist|all` and `--vars "A B"`. `allowlist` (default) gives the editor's terminals only the listed
non-secret variables; `all` copies everything from `/etc/environment`, secrets included (convenient, wider blast radius: see the
security doc); `none` gives them nothing extra. Systemd services do not read `/etc/environment` by themselves.

It creates the mode-700 socket directory, writes the editor's config (socket, mode 600, `auth: none`), installs the systemd unit
(groups cleared), sets the sudo cache to 5 minutes, installs the hourly verification timer and records the version in
`/etc/codelikehome/installed-version`. Re-running it only rewrites what differs and restarts the editor only if its unit changed.

Check: `ls -l ~/.local/run/code-server/` shows `srw-------`; `ss -tlnp | grep -i code` shows nothing.

Notifications on failure (optional): put an executable at `/etc/codelikehome/notify` that takes the message as its first argument
(for example a script that calls your push-notification service).

## 3. The forwarder

```sh
cp .env.example .env     # set CLH_IMAGE, CLH_TAG, CLH_UID (id -u YOU), CLH_SOCKET_DIR, CLH_ALLOW (your bastion's container name)
docker compose -f compose/codelikehome.yaml --env-file .env up -d
```

or `include:` the compose file from your own project (the header of `compose/codelikehome.yaml` shows how). The network
`vscode_net` is `internal: true`; attach **only** the forwarder and the bastion. To build the image yourself instead of pulling
it: `docker build -t codelikehome-forwarder:local forwarder/`, then set `CLH_IMAGE=codelikehome-forwarder`, `CLH_TAG=local`,
`CLH_PULL_POLICY=never`.

## 4. Warpgate

1. Attach the Warpgate container to `vscode_net` (in addition to its normal networks), and make sure its container name (or network alias) matches `CLH_ALLOW`.
2. In the admin UI: **Config → Targets → Add → Web (HTTP)**: name `vscode`, URL `http://vscode:8443`, TLS off.
3. Create a role, assign it to the target and to your user only. **Do not** share the role with other users.
4. Set a credential policy that requires **SSO and TOTP** for HTTP, and add a TOTP credential.
5. Behind a proxy, `warpgate.yaml` needs `external_host`, `http.external_host`, `http.external_port: 443` and `trust_x_forwarded_headers: true`, otherwise SSO redirects and cookies break.
6. Shorten session timeouts. If you use SSH through Warpgate, do not forward its SSH port on your router.

## 5. Public access (optional)

Add the site from `examples/Caddyfile.warpgate`. Warpgate authenticates itself, so **do not** put `forward_auth` in front of it.
Password login is allowed only from private addresses; remote users must use SSO. `flush_interval -1` and the `no-transform`
header matter: the workbench is mostly a websocket, and some CDNs rewrite scripts and break it. If you use a tunnel, point it at
the proxy, not at the bastion or the editor.

## 6. Open it

Browse to `https://warpgate.example.com`, sign in (SSO + TOTP), pick the `vscode` target. Direct link:
`https://warpgate.example.com/?warpgate-target=vscode`.

## 7. Verify

```sh
sudo sh host/verify.sh --user YOU --container vscode --bastion warpgate --network vscode_net
```

Every line should say `ok`. The timer runs the same script hourly (`systemctl list-timers codelikehome-verify.timer`;
`journalctl -u codelikehome-verify`).

| Check | Expect |
|---|---|
| in the editor terminal: `id` | no `docker` group |
| in the editor terminal: `docker ps` | permission denied |
| in the editor terminal: `sudo -n true` | asks for the password |
| from another LAN machine: `curl http://<host>:8443` | connection refused (nothing is published) |
| a throwaway container on `vscode_net`: connect to `vscode:8443` | connection closed, and `docker logs vscode` prints `refused connection from …` |
| open the site signed out | the bastion's login, never the editor |
