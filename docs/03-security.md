# Security: benefits, risks, and what remains

> This page describes the default `strict` profile. `balanced` and `workstation` deliberately relax the group, umask, environment
> and sudo-cache rows below; see [06-profiles.md](06-profiles.md) for what each one gives up. If the editor is in the `docker`
> group, treat it as root.

The summary: this design makes the editor **hard to reach** and **hard to turn into root**, but it does not make a signed-in
session **safe to lose**. Anyone who gets a working session has a shell as your user. Everything below is about shrinking the
number of ways to get that session and limiting what it can do afterwards.

## Benefits

| Property | What it buys you | Where it comes from |
|---|---|---|
| No TCP listener for the editor | nothing to port-scan, nothing on the LAN or WAN can connect to it directly | socket only, mode 600, directory 700 |
| Socket reachable only by your uid | a compromised container or other local user cannot open it; the forwarder works only because it runs as the same uid | |
| Private, internal network shared only with the bastion | even a compromised neighbouring container cannot reach the forwarder | `internal: true`, members Warpgate + forwarder |
| Single front door with SSO + TOTP | a stolen password alone is not enough; password login is not offered from the internet | your bastion configuration |
| Editor has no password of its own | no second, weaker, forgotten login surface; no credential to leak from the editor | by choice |
| Editor user has **no supplementary groups** | the editor cannot talk to Docker (root-equivalent), nor use group-granted device or log access | |
| Privileged actions need `sudo` + password, 5-minute cache | a hijacked session cannot silently become root | |
| Forwarder is tiny and locked down | read-only, no capabilities, no-new-privileges, 64 MB, uid 1000, one mount; it only copies bytes | |
| No third-party forwarder image | one less supply-chain input; the relay is about 60 lines you can read | |
| Hourly root-owned verification | drift in socket permissions, groups, network membership or the sudo cache raises a notification | install.sh timer |
| Warpgate logging | an audit trail of who opened the editor and when | bastion setting; check what it records for HTTP targets |
| No router port forwarded | a Cloudflare tunnel connects outward; nothing is exposed inbound | if you use the tunnel |

## Risks and residual exposure

Ordered roughly by how much they should worry you.

### 1. A session is a shell as you

Whoever is in the editor can run any command as your user, read every file your user can read, and install anything in your home.
`sudo` is gated by a password, but:

* a shell can **capture that password** (shell alias or function, a fake `sudo`, terminal key capture by an extension);
* an attacker who is in your session while the 5-minute cache is warm gets root without it.

Treat a live session as equivalent to your account. The mitigations are about how easily a session is obtained (SSO + TOTP, short
Warpgate timeouts) and how quickly drift is noticed (the verification timer, a file-integrity tool), not about containing the shell.

### 2. The editor can rewrite what root later runs

Your user can edit compose files, the reverse-proxy config and scripts. Those are applied later by whoever runs
`sudo docker compose up` (you). A malicious edit waits for you to apply it. Nothing here stops that; consider a file-integrity tool on those paths. Read diffs before you apply them, especially after a session you do not remember.

### 3. Secrets in the editor's environment

If the unit loads the whole of `/etc/environment` (`--env all`), every terminal and every extension inherits whatever
sits there: tokens, passwords, API keys. This makes terminals behave exactly like a login shell and is convenient, but it
widens the blast radius of any malicious extension or pasted command. The example unit loads a short allowlist instead
(`/etc/code-server/environment`, written by `install.sh`). **Prefer the allowlist**; if you load everything, keep real secrets out of
`/etc/environment`.

The workspace folder also matters: opening the compose directory exposes every `.env` and secrets file in it to the editor and its extensions.

### 4. Extensions

code-server uses the Open VSX registry, not Microsoft's Marketplace. Extensions run server-side with your user's permissions.
A malicious or compromised extension can read files, read the environment and use the network. Install few, from publishers you
trust, and review updates. There is no sandbox between an extension and your account.

### 5. Warpgate is a single point of both defence and failure

If Warpgate (or the SSO account feeding it) is compromised, the editor is reachable. The same applies to the identity provider
account: a stolen Google account with a TOTP-less or SMS-recoverable second factor weakens everything. Keep Warpgate current,
use a hardware key or authenticator app on the identity account, and keep its admin UI behind the same controls.

### 6. TLS ends at Cloudflare (if you use the tunnel)

Cloudflare terminates TLS, so it can in principle see decrypted traffic, including everything typed in the editor and file contents.
That is a trust decision about a third party. If it matters, replace the tunnel with a VPN (WireGuard/Tailscale) and keep only the
LAN path, or terminate TLS yourself on an exposed port and accept the port exposure. This is the largest *design* trade-off.

### 7. The client device

The tablet holds a signed-in session and a browser profile. Lost or stolen with the screen unlocked, it is your account.
Browsers sync passwords and cache pages; third-party keyboards see everything typed, including the sudo password.
See the tablet guide for specifics.

### 8. Browser-side isolation inside code-server

Webviews (Markdown preview, notebooks, extension panels) in code-server are served from the same origin as the workbench, so
the isolation between a webview and the editor is weaker than in desktop VS Code. Be cautious with untrusted notebooks,
Markdown, or extensions that render remote content. Check this against the code-server version you run; the behaviour has changed between releases.

### 9. Websocket and header handling

code-server checks the `Origin` of websocket upgrades, so a page on another site cannot drive your session even if you are signed in.
Through Warpgate and Caddy the `Host` and forwarded headers must be preserved (`header_up Host {host}` in the Caddyfile). A proxy
that rewrites them can make the editor refuse connections or, worse, push you to disable the check. Do not disable it.

### 10. Operational mistakes

* Publishing the forwarder's port (`ports:`) removes the whole benefit. Attaching another container to `vscode_net` used to do the same; with `FORWARD_ALLOW=warpgate` the forwarder now refuses it (see "Only the bastion may connect"). Keep the network `internal` anyway.
* Running the editor as root or adding the user to `docker` makes the editor root-equivalent in one step. The unit clears groups to prevent this; re-check after OS upgrades or user changes.
* Letting sessions live for days. Short Warpgate and idle timeouts are the best cheap control.

## Only the bastion may connect

Three layers keep everything except Warpgate away from the editor:

1. **Network:** `vscode_net` is internal, and only Warpgate and the forwarder are on it. The forwarder publishes no port.
2. **Peer check:** `FORWARD_ALLOW=warpgate` makes the forwarder drop any connection whose source address is not Warpgate's. A container added to the network later (by mistake or by an attacker with Docker access) still cannot reach the editor.
3. **Socket:** mode 600 in a 700 directory, owned by your user. Nothing but your user and root can open it directly.

What this closes: the "something else joined `vscode_net`" mistake, and lateral movement from another container.
What it does not close:

* **Host processes running as the editor's user** can open the socket directly: the editor's own terminals, other tools you run, anything an attacker runs after landing as that user. A mode-600 socket cannot exclude its own owner, and a process already running as you can do everything the socket would let it do anyway (it can read your files and run commands as you), so for a same-user process the socket is not the boundary that matters.

  If you want "only the bastion and root can open the socket, nothing running as my login", the editor needs its own account:

  * run code-server as a dedicated user (for example `codeserver`), so the socket and its directory belong to that account and your login user cannot open them;
  * the forwarder then runs as that dedicated uid (`CLH_UID`), and your normal user reaches the editor only through the bastion like everyone else;
  * the cost is that the editor is no longer *you*: it has its own home and files, and needs ACLs or group access to the projects it should edit. It also stops being a drop-in equivalent of Remote-SSH to your own account.

  The installer's `--user` accepts any account, so this is a matter of choosing a different user. Leave the editor account out of `docker` and `sudo` unless you need them.
* **Root** can do anything.
* **Warpgate itself, or a Warpgate account allowed to the `vscode` target.** Scope the `vscode` target to one role held by one person, so no other Warpgate user (or shared role) can open it.
* **Anything that can talk as Warpgate**, i.e. a compromised Warpgate container.

**SSH is a separate path.** A desktop VS Code Remote-SSH session starts its own server (`~/.vscode-server`) as the connecting user, with that user's full group set, which normally includes `docker`. It is not code-server and is not on the bastion path. Any shell a user gets over SSH has the same power, so "only the bastion" holds for the browser editor, not for SSH. Close that gap by sending SSH through the bastion as well (Warpgate has an SSH target) and keeping direct sshd off the LAN or key-only.

## How it compares

| Setup | Exposure | If the session is stolen |
|---|---|---|
| This design | none directly; bastion with SSO + TOTP | shell as your user; root needs the password |
| code-server on a TCP port with a password | the port, plus a password to brute-force | same |
| code-server in a container with the Docker socket | that port and root-equivalent | host root, immediately |
| Remote-SSH from a PC (key-based sshd) | SSH port or VPN | shell as your user; same sudo story |
| SSH via Warpgate from a PC | bastion | same, with an audit trail |

Compared with Remote-SSH, the security properties are close: both give a user shell, and both depend on how well the front
door is guarded. The differences are the surface (a web app and websocket instead of sshd), the client (a browser holding a
session instead of a key on a PC), and the third parties in the path (Cloudflare, if used).

## A reasonable hardening checklist

1. Use the **allowlist** environment file, not all of `/etc/environment`.
2. Require **SSO + TOTP** in Warpgate; shorten session and idle timeouts.
3. Keep password login LAN-only (the Caddyfile snippet) and consider a hardware key at the identity provider.
4. Consider a **VPN instead of a public tunnel**, or at least Cloudflare Access in front, so the bastion login page is not public.
5. Keep the verification timer running and give it a notifier (`/etc/codelikehome/notify`) you actually read.
6. Few extensions, and no extension that needs your secrets.
7. Open a narrow workspace folder, not your whole home or the directory full of `.env` files.
8. On the tablet: screen lock with a short timeout, device encryption, a trusted keyboard, no browser password manager for the bastion, and sign out when done.
9. Re-run the checks in `02-setup.md` after OS updates.
10. Update Warpgate, code-server and the forwarder image regularly (see `05-updating.md`).

## What this does not protect against

* Anyone who already has a shell on the host.
* A compromised identity provider account that can also satisfy the second factor.
* A malicious extension or a pasted command you run yourself.
* Keystroke capture on the client device.
* Zero-days in code-server, Warpgate, Caddy or the browser.
