# CodeLikeHome

A full VS Code session on your own server, reachable from any browser (an Android tablet included) and gated by a bastion with
single sign-on and a one-time code. It feels like Remote-SSH from a PC: files, terminal, extensions and language servers all run
on the server; the tablet is only a screen and a keyboard.

This repository is the install kit and the reasoning: a tiny forwarder image, a compose file, host install scripts, a
verification script, and an honest account of what the design protects and what it cannot.

| Read | For |
|---|---|
| [docs/01-architecture.md](docs/01-architecture.md) | what runs where, the request path, why each piece exists |
| [docs/02-setup.md](docs/02-setup.md) | install, step by step |
| [docs/03-security.md](docs/03-security.md) | benefits, risks, who can and cannot reach the editor |
| [docs/04-tablet-guide.md](docs/04-tablet-guide.md) | using it from an Android tablet or phone, and how it compares with Remote-SSH |
| [docs/05-updating.md](docs/05-updating.md) | taking a new release into an existing install |

## The idea in one paragraph

code-server (VS Code in the browser) runs **on the host as your normal user**, with no `docker` group, and listens on a
**mode-600 Unix socket**, not a network port. A 100-line relay in a locked-down container copies bytes between a private Docker
network and that socket, and accepts connections only from your bastion. The bastion (this guide uses
[Warpgate](https://github.com/warp-tech/warpgate); anything that can proxy HTTP and require SSO plus a second factor works) is the
only other thing on that network. There is exactly one way to the editor, and it passes through your identity provider.

```
browser / Android tablet
        │ HTTPS
        ▼
reverse proxy (+ optional tunnel) ──► bastion  (SSO + TOTP, session logging)
                                        │  http://vscode:8443   (vscode_net: internal, only the bastion and the forwarder)
                                        ▼
                              forwarder container  (read-only, no caps, peer allow-list)
                                        │  /run/code-server/code-server.sock  (mode 600)
                                        ▼
                    code-server on the HOST, as your user, no supplementary groups
                                        │  sudo + password for anything privileged
                                        ▼
                                     the server
```

## What is in the repository

| Path | What |
|---|---|
| `forwarder/` | the relay (`forward.py`) and its Dockerfile; published as `ghcr.io/<owner>/codelikehome-forwarder:<version>` |
| `compose/codelikehome.yaml` | the forwarder service and its internal network, configured through `.env` (see `.env.example`) |
| `host/install.sh` | idempotent installer for the host half (systemd unit, socket directory, config, sudo cache, verification timer) |
| `host/verify.sh` | read-only checks; also run hourly by a root timer the installer sets up |
| `examples/Caddyfile.warpgate` | a reverse-proxy site for the bastion |
| `VERSION`, `CHANGELOG.md` | what you are running and what changed |

## Quick start

```sh
git clone https://github.com/OWNER/CodeLikeHome && cd CodeLikeHome
cp .env.example .env && $EDITOR .env                          # image tag, editor uid, socket directory, bastion name
sudo sh host/install.sh --user YOU --dry-run                  # see exactly what it would change
sudo sh host/install.sh --user YOU
docker compose -f compose/codelikehome.yaml --env-file .env up -d
sudo sh host/verify.sh --user YOU
```

Then attach your bastion to `vscode_net` and add the web target. Full steps are in [docs/02-setup.md](docs/02-setup.md).

## Scope and honesty

This hardens the **path to** the editor and limits what the editor process can do as root. It does not make a signed-in session
safe to lose: whoever is in the editor has a shell as the editor's user. Read [docs/03-security.md](docs/03-security.md) before
you expose it to the internet.

Licence: MIT.
