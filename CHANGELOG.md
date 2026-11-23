# Changelog

## Unreleased

* `host/install.sh --gate [--users a,b]`: a PAM sign-in page (`host/gate.py`) in front of per-user editors (`code-server@USER`, started on demand by `host/run-editor.sh`). Each
  user gets their own uid, home, environment and the profile's groups (Docker with `workstation`), and can sign in as a different user at any time. Per-request cookie checks, CSRF
  token, throttling, session expiry; WebSocket upgrades only are relayed raw. `verify.sh` checks the gate and every user's editor. See docs/07-multi-user-login.md.

* `host/install.sh --profile strict|balanced|workstation`, with `--groups`, `--umask` and `--sudo-timeout` overrides. `strict` is the
  default and is identical to the previous behaviour. The unit's groups and umask are now templated; the choice is recorded in
  `/etc/codelikehome/profile`. A warning is printed whenever the editor will be in the `docker` group.
* `host/verify.sh` checks against the recorded profile (groups, umask, sudo cache) and also verifies the editor's umask; it reports
  root-equivalence by choice as `info`, not `FAIL`.
* docs/06-profiles.md: the trade-offs, rootless-Docker and Sysbox guidance, and the symptoms agents see under each profile.

## 0.1.0

First release.

* `forwarder/`: TCP-to-Unix-socket relay image with a per-connection peer allow-list (`FORWARD_ALLOW`) and an accepted/refused connection log.
* `compose/codelikehome.yaml`: the locked-down forwarder service on an internal network, configured through `.env`.
* `host/install.sh`: idempotent installer for the editor's systemd unit (groups cleared), socket directory, config, 5-minute sudo cache and an hourly root verification timer; `--dry-run` shows what would change.
* `host/verify.sh`: read-only checks of socket permissions, network membership, published ports, allow-list and the editor's groups.
* Docs: architecture, setup, security analysis (what it protects and what it cannot), tablet guide, updating.
