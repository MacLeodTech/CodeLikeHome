# Multi-user sign-in (`--gate`)

By default one editor runs as one user, and the bastion is the only gate. With `--gate` the editor becomes a **sign-in page**:
you give a username and password, and you get *that user's* editor: their uid, home, shell, environment and, per the
[profile](06-profiles.md), their groups. With `--profile workstation` that includes `docker`, so the terminal behaves like
`ssh you@server`. Signing in as a different user later just means signing in again.

```
browser ─► bastion (SSO + TOTP) ─► forwarder ─► codelikehome-gate (root, PAM) ─► code-server@alice  (socket, mode 600, runs as alice)
                                                                            └──► code-server@bob    (socket, mode 600, runs as bob)
```

## Install

```sh
sudo sh host/install.sh --user plex --gate --users plex,alice --profile workstation --dry-run
sudo sh host/install.sh --user plex --gate --users plex,alice --profile workstation
```

* `--user` owns the socket the forwarder opens (it is the forwarder's uid, `CLH_UID`); nothing else changes in `compose/` or `.env`.
* `--users` is who may sign in. It is written to `/etc/codelikehome/gate.users`; edit that file to add or remove people (no restart:
  a removed user's sessions stop working on their next request). Accounts with uid < 1000, `root`, and `nologin` shells never sign in.
* Each user needs code-server for themselves (`curl -fsSL https://code-server.dev/install.sh | sh -s -- --method=standalone` as that
  user) or a system-wide one in `/usr/local/bin`.
* Moving from the single-editor install stops and disables `code-server.service` (open editors end); the gate takes over the same
  socket path. To go back, re-run the installer without `--gate`, then `systemctl disable --now codelikehome-gate`.

## What happens at sign-in

1. The gate checks the password with PAM (service `codelikehome`: `common-auth` and `common-account`, so the same rules as a console login,
   including locked and expired accounts).
2. It starts `code-server@USER`. `run-editor` (as root) creates `/run/codelikehome/users/<uid>/` (mode 700, owned by that user) and drops
   to the user with `setpriv`, the profile's groups and umask.
3. The browser gets a random session cookie (`HttpOnly`, `Secure`, 12 hours, 2 hours idle). The gate checks it on **every** request, strips it
   before the editor sees it, and relays only WebSocket upgrades as raw streams. A pipelined or pooled connection cannot slip a second
   request past the check.

Throttling: 5 wrong passwords for a name pauses that name for the rest of 10 minutes, 30 wrong overall pauses everyone. Wrong name,
unlisted user and wrong password look identical and cost the same time. Logs (`journalctl -u codelikehome-gate`) record sign-ins,
failures and editor starts, never passwords.

## What it costs you (read this)

* **A root service now checks passwords.** It is ~500 lines of standard-library Python plus `libpam`, sandboxed (`NoNewPrivileges`, read-only
  `/etc` and `/usr`, only the run directories writable), but a bug in it is a root bug. Keep the bastion in front: the gate adds a *second*
  factor-less password check, it does not replace SSO + TOTP.
* **A password guesser who passes the bastion can lock a user out** of the sign-in page for ten minutes. That is the price of not letting them guess forever.
* `workstation` + `docker` in the user's groups means a signed-in session is root-equivalent, as before; now for every listed user. List only people you would give `sudo`.
* Editors stay running after sign-out until you stop them (`systemctl stop code-server@alice`), so open work is not lost.
* Check in your own browser that the bastion passes `Set-Cookie` and `Location` through: sign-in, reload, and the editor should open without asking again.

`host/verify.sh` (and the hourly timer) checks the gate service, that its files are root-owned and not writable by others, that every
`/run/codelikehome/users/*` is mode 700, and each running editor's groups and umask against the profile.
