# Architecture

## The pieces

| Piece | Where it runs | Job | Why this and not something else |
|---|---|---|---|
| **code-server** | the host, as your user, via systemd | editor, terminal, extension host, file access | Running it on the host (not in a container) means it edits the real files with your real permissions, like a Remote-SSH server would. No bind-mount gymnastics, no container root. |
| **Unix socket** (`~/.local/run/code-server/code-server.sock`, mode 600, directory mode 700) | host filesystem | the only way in to code-server | A socket has no port to scan and no IP to reach. Only the owning uid (and root) can open it. |
| **Forwarder** (`vscode`) | a container | copies TCP bytes from `vscode_net:8443` to the socket, for allowed peers only | A container on a private network cannot open a host socket by itself. The relay bridges the two and understands nothing about the traffic, so websockets pass through unchanged. |
| **`vscode_net`** | Docker network, `internal: true` | joins the forwarder to the bastion and nothing else | Internal networks have no route out and no published ports. |
| **Bastion** (Warpgate in this guide) | a container | login (SSO + TOTP), authorisation, session logging | One hardened front door for several internal services. The editor has no password of its own on purpose. |
| **Reverse proxy** | a container | TLS, public hostnames | Whatever you already use. |
| **Tunnel** (optional) | a container | publishes the proxy without opening a router port | No inbound port forwarding. Trade-off: the tunnel provider terminates TLS (see the security doc). |
| **sudo with a 5-minute cache** | host | gate for anything privileged | The editor user is not in the `docker` group, so Docker control needs `sudo` and the password. |
| **Verification timer** | root systemd timer | hourly `host/verify.sh`; optional notification on failure | A root-owned check the editor's user cannot edit notices drift in socket permissions, groups, network membership. |

## Request path

1. The browser opens the bastion's address and signs in: identity provider (SSO), then a TOTP code.
2. The bastion picks the **web (HTTP) target** `vscode`, URL `http://vscode:8443` (TLS off; the hop is an internal network) and proxies browser traffic to it, including the websocket the workbench lives on.
3. `forward.py` accepts the connection on 8443, checks the peer is the bastion (`FORWARD_ALLOW`), opens `/run/code-server/code-server.sock` and copies bytes both ways.
4. code-server answers. It trusts the connection because it can only have come through the relay, which can only be reached by the bastion.
5. Everything the editor does (open a file, run the terminal, start a language server) happens on the host as your user.

## Network membership

* `vscode_net`: **the bastion and the forwarder only.**
* The bastion also sits on its own default network (for the proxy) and may join other private networks for other targets.
* code-server is on **no** network. It is not a container and has no TCP listener.

## The forwarder container

Read-only root filesystem, all capabilities dropped, `no-new-privileges`, 64 MB memory limit, one volume (the socket's directory),
runs as the editor's uid so it may open the mode-600 socket. Healthcheck connects to its own port.

Environment: `FORWARD_UNIX` (socket path), `FORWARD_LISTEN` (port, default 8443), `FORWARD_ALLOW` (comma-separated host names whose
addresses may connect), `FORWARD_MAX` (simultaneous connections, default 64; further ones are dropped and logged).

**Peer allow-list.** The relay resolves each allowed name on every connection and drops any peer whose address is not in the
result, so network membership is not the only thing keeping other containers out. A recreated bastion with a new address keeps
working. Every accepted and refused connection is logged (`docker logs vscode`), which gives you a record of who reached the editor
and when.

The relay gives up on a connection that carries no data for 15 minutes and does no authentication of its own: reaching it at all
already means you passed the bastion.

## The host side

The unit starts as root only long enough for `setpriv --reuid=<uid> --regid=<gid> --clear-groups` to drop to your user with **no
supplementary groups**. `NoNewPrivileges` is deliberately off, because `sudo` needs its setuid bit. Light sandboxing options are
on (`ProtectKernelModules`, `ProtectKernelLogs`, `ProtectControlGroups`, `LockPersonality`, `RestrictRealtime`, `UMask=0077`).
`host/install.sh` writes all of this; you can read the templates in `host/*.in`.
