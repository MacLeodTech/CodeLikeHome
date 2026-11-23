# Roadmap: finishing the multi-user sign-in (`--gate`)

Status (2026-11-23): built and tested in the repo, **not installed anywhere**. Prod still runs the single `code-server.service`.

## Already checked (no root needed)

- 31 unit/integration tests pass: login, CSRF, throttling, session expiry, per-user routing, user switching, pipelining/smuggling guards, WebSocket relay.
- `ruff check .` clean; `shellcheck -S warning` clean on `install.sh`, `run-editor.sh`, `verify.sh`; `sh -n` on all scripts.
- `systemd-analyze verify` accepts the rendered `code-server@.service` and `codelikehome-gate.service`.
- **Real code-server behind the gate** (a throwaway instance on a temp socket, fake password check): sign-in started the editor on demand, `/` redirected to the
  folder, the workbench page and static assets loaded (200), `/healthz` answered, the WebSocket upgrade returned 101, and a request without the cookie got 401.
- The ctypes PAM call runs against the real `libpam` and rejects a bad password without crashing.

## To do on the server (needs root and your real passwords)

Do these in order; each has a check. Expect open editor sessions to drop at step 3.

1. **Pick the users.** Decide who goes in `--users`. Each must have code-server: as that user run
   `curl -fsSL https://code-server.dev/install.sh | sh -s -- --method=standalone` (or install one system-wide in `/usr/local/bin`).
   Check: `ls ~USER/.local/bin/code-server`.
2. **Dry run.** `sudo sh host/install.sh --user plex --gate --users plex,USER2 --profile workstation --dry-run`. Read the list of files it would write and the warnings
   (docker group, root password-checking service, old `code-server.service` being stopped).
3. **Install.** Same command without `--dry-run`. It stops/disables `code-server.service` and starts `codelikehome-gate`.
   Check: `systemctl status codelikehome-gate`; `ls -l ~plex/.local/run/code-server/` shows the gate's `srw-------` socket.
4. **Sign in as plex** through Warpgate in a browser with your real password.
   Check: the editor opens; in its terminal `id` matches your SSH session (includes `docker`), `docker ps` works, `echo $HOME` is yours.
   If it fails: `journalctl -u codelikehome-gate -u code-server@plex -n 50`.
5. **Warpgate pass-through.** Reload the tab and open the editor in a new tab: it must not ask you to sign in again, and there must be no redirect loop
   (this proves Warpgate forwards `Set-Cookie` and `Location`). If it loops, the fix is in Warpgate's cookie/redirect handling, not the gate.
6. **Second user.** Open the sign-in page while signed in and sign in as the other user.
   Check: their home, their groups, their own settings; `ps -u USER2 | grep code-server`.
7. **Bad paths.** Wrong password shows the generic error; five wrong passwords pause that user for ~10 minutes; a user not in `/etc/codelikehome/gate.users` cannot sign in.
8. **Verify script.** `sudo sh host/verify.sh --user plex --network docker_vscode_net` (prod's network is `docker_vscode_net`; the default name fails today regardless of this change).
   Expect `info` that editors are root-equivalent (docker group, by choice) and no `FAIL`.
9. **Rollback rehearsal (once).** Re-run the installer without `--gate`, then `sudo systemctl disable --now codelikehome-gate`; the old single editor returns. Then re-install with `--gate`.
10. **Commit and release** from the repo once the above passes. The release workflow builds the forwarder image on pushes to `main`; the forwarder itself did not change.

## Later / ideas

- Stop idle editors automatically after sign-out or N hours without requests (today they run until `systemctl stop code-server@USER`).
- Optional per-user TOTP at the gate, so the second factor does not depend only on the bastion.
- A `verify.sh` check that a known-bad password is refused (needs a test account).
- Rootless/Sysbox Docker per user instead of the `docker` group (see docs/06-profiles.md) if root-equivalence becomes a concern.
