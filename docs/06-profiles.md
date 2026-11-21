# Profiles: choosing how much friction to accept

The default install is the strictest one: the editor cannot reach Docker, its files are private to you, and anything privileged
needs your password. That is the right default for a host exposed to the internet. It also makes the editor behave differently
from a normal login shell, and people (and coding agents) running inside it hit that difference. `host/install.sh --profile`
lets you choose where to sit between "hard to abuse" and "behaves exactly like my own login".

## The three profiles

| | `strict` (default) | `balanced` | `workstation` |
|---|---|---|---|
| Supplementary groups | none | none, plus any you list with `--groups` | all of your login groups (`setpriv --init-groups`) |
| Umask of every editor process | `0077` (new files private) | `0027` (group may read) | `0022` (world-readable, like a normal login) |
| Environment passed to terminals | allowlist (`LANG LC_ALL TZ EDITOR`) | allowlist, extend with `--vars` | everything in `/etc/environment` |
| sudo password cache | 5 min | 15 min | 60 min |
| Docker | no | only if you add `--groups docker` | yes, if your account is in `docker` |
| If the session is stolen | shell as you, no Docker, no silent root | depends on groups | **root-equivalent** if `docker` is included |

Everything is overridable one at a time: `--groups none|login|g1,g2`, `--umask 0027`, `--env none|allowlist|all`,
`--vars "A B"`, `--sudo-timeout MIN`. An explicit option always wins over the profile.

```sh
sudo sh host/install.sh --user YOU --profile balanced --groups docker --dry-run
sudo sh host/install.sh --user YOU --profile workstation
```

The choice is written to `/etc/codelikehome/profile`, and `verify.sh` (and the hourly timer) checks the running editor against
**that** profile: groups it should and should not have, its umask, the sudo cache. It reports `info` when the editor is
root-equivalent by choice and `FAIL` only on drift from what you asked for.

## What each loosening gives up

**Groups.** `docker` is root: `docker run -v /:/host` is a password-free root shell. With it, the sudo password and its cache
protect nothing, and the tight umask and env allowlist only slow an attacker down. Other groups are narrower (`adm` reads logs,
`video`/`render` use GPUs, `dialout` serial ports); add those freely if you need them.

**Umask.** `0077` makes every file the editor creates unreadable to other users and containers. That breaks bind mounts read by a
container user, files shared with a service account, and tests that create `0644` files and then assert the mode (the test sees
`0600`). `0027` or `0022` fixes these. It costs little: the files are still inside your home directory.

**Environment.** Systemd services do not read `/etc/environment`, so by default the editor's terminals lack variables a login
shell has. `all` copies every variable in `/etc/environment`, secrets included; they are then visible to every extension and
process in the editor.

**sudo cache.** A longer cache means a longer window after you type the password in which a hijacked session can use it.

## Docker without the docker group

If what you need is `docker build` and `docker compose` in the editor, not host root, avoid the group and give the editor a
Docker daemon that cannot reach the host:

* **Rootless Docker for the editor user** (`dockerd-rootless-setuptool.sh install`), with `DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock`
  added to the editor's environment (`--vars DOCKER_HOST`). Containers run in a user namespace, so root in a container is not
  root on the host. Caveats: it is a separate daemon (separate images and containers from the system Docker), and ports below
  1024, some storage drivers and some networking features differ. Needs `XDG_RUNTIME_DIR` set for the editor, which a systemd
  system service does not provide; export it in the unit or use a user service.
* **A Sysbox-backed daemon** (Docker's `sysbox-runc` runtime): containers run as a nested system with user-namespace mapping.
  It suits setups that need Docker for builds without giving the editor host root.

Neither is installed or configured by `install.sh`; they are host changes that are yours to make. Use `balanced` without
`--groups docker` and add `--vars DOCKER_HOST`.

## For coding agents running in the editor

Agents in the editor see exactly what the editor's process sees. If an agent reports one of these, it is the profile, not a bug
in its project:

| Symptom | Cause | Fix |
|---|---|---|
| `permission denied ... /var/run/docker.sock` | editor has no `docker` group | `--groups docker` (root-equivalent) or rootless Docker |
| a test expecting mode `0644` fails with `0600` | umask `0077` | `--umask 0022` or `0027` |
| `... is not set in this shell`, `JAVA_HOME` undefined | env allowlist | `--vars "NAME ..."` or `--env all` |
| `sudo` asks for a password and cannot continue | by design | run the privileged step yourself |

A strict install should tell agents these limits up front (for example in `CLAUDE.md`: "no docker group, umask 077, no
sudo; do not work around it, ask") so they stop retrying and report the limit.

## Choosing

* Internet-reachable, shared host, or the machine holds things you cannot lose: `strict`, with rootless Docker if needed.
* A home server behind SSO and TOTP where you want Docker and shared files: `balanced --groups docker`, and accept that a stolen
  session is root.
* A single-user machine where the tablet should be a drop-in for Remote-SSH: `workstation`.
