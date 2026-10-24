# Changelog

## 0.1.0

First release.

* `forwarder/`: TCP-to-Unix-socket relay image with a per-connection peer allow-list (`FORWARD_ALLOW`) and an accepted/refused connection log.
* `compose/codelikehome.yaml`: the locked-down forwarder service on an internal network, configured through `.env`.
* `host/install.sh`: idempotent installer for the editor's systemd unit (groups cleared), socket directory, config, 5-minute sudo cache and an hourly root verification timer; `--dry-run` shows what would change.
* `host/verify.sh`: read-only checks of socket permissions, network membership, published ports, allow-list and the editor's groups.
* Docs: architecture, setup, security analysis (what it protects and what it cannot), tablet guide, updating.
