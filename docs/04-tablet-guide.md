# Using it from an Android tablet (or phone)

There is no Android build of VS Code with Remote-SSH. This setup is the closest thing: the **whole development environment
stays on the server**, as it does with Remote-SSH, and the tablet shows it through the browser. Your files, terminal,
language servers, debuggers, git credentials and extensions run server-side as your user.

## What you get compared with Remote-SSH from a PC

| | Remote-SSH from a PC | This setup on a tablet |
|---|---|---|
| Where code, terminal and extension host run | server | server |
| Where the UI runs | desktop VS Code | browser (Chrome) or an installed PWA |
| Language servers, linters, debuggers | server | server |
| Extensions | Microsoft Marketplace | Open VSX (a few popular ones are missing, e.g. the closed-source Microsoft remote and some AI ones) |
| Extensions that run on the *client* side (UI themes, keymaps) | work | work (they run in the page) |
| Port forwarding | built in | `/proxy/<port>/` path through the editor; works for web apps you run |
| Terminal | integrated | integrated, same shell |
| Native keybindings | all | some are taken by the browser/OS unless installed as an app and using a hardware keyboard |
| Clipboard | native | needs HTTPS and a user gesture; works over the bastion |
| Settings sync | account based | per-server settings in the browser profile and the user data dir |
| Offline | no | no |

It "feels like" Remote-SSH because the architecture is the same (thin client, fat server). The differences are mostly input and
browser limits, not capability.

## Make it feel like an app

1. Open the bastion URL in **Chrome** and sign in.
2. Open the `vscode` target.
3. Chrome menu → **Install app** (or **Add to Home screen**) so it runs in its own window without browser chrome. VS Code on the web
   offers this as a PWA over HTTPS. A standalone window also frees up shortcuts the browser would otherwise take.
4. Prefer **landscape** and a hardware keyboard.

Samsung tablets in DeX mode, or any tablet with a desktop-style window mode, make this much closer to a PC.

## Input

* **Use a hardware keyboard.** The on-screen keyboard has no Esc, Ctrl, Alt or Tab row, so the terminal and vim-style editing are painful. A Bluetooth keyboard is the single biggest improvement.
* A mouse or trackpad helps with the editor's small hit targets; touch alone works for reading and light edits.
* Browser-reserved shortcuts (Ctrl+W, Ctrl+T, Ctrl+N) behave better in the installed app than in a tab. Remap anything that still collides in VS Code's keyboard shortcuts, or use the command palette (F1 or Ctrl+Shift+P).
* Pinch-zoom the page to a comfortable size, or set `window.zoomLevel` in settings.
* Long-press replaces right-click on touch screens.

## Sessions and reconnection

* The workbench reconnects after a dropped connection and restores unsaved editor state. Terminals reconnect but long-running processes in them belong to the shell, so run important jobs in `tmux` or as services; the browser disappearing should not kill them.
* The forwarder drops a connection that carries no data for 15 minutes; code-server keeps the websocket alive with pings, so normal use is unaffected. Warpgate's own session timeout will eventually ask you to sign in again (check the value you configured).
* On Android, a backgrounded tab can be frozen or discarded. The installed app is more resilient than a tab; expect a reconnect when you return.

## Security habits that matter on a tablet

* **Screen lock with a short timeout** and device encryption on. An unlocked tablet with a live session is your account.
* **Do not let the browser or a third-party keyboard remember** the bastion password or learn your sudo password. Use the system keyboard, or a hardware one; avoid cloud-synced keyboards for terminals.
* Use a **passkey or authenticator app** for the identity provider, and keep the TOTP code on a device other than the tablet if you can.
* **Sign out** of Warpgate when finished; do not choose "stay signed in" on a shared or travelling device.
* Avoid public Wi-Fi without a VPN if you do not trust the path (the site is HTTPS, but a VPN adds a second layer and hides the hostname).
* Be careful with the sudo password: the 5-minute cache means you type it rarely, which also means it is easy to forget it is live. After privileged work, `sudo -k` ends the cache immediately.
* Do not paste secrets into the editor's terminal on a device whose clipboard is shared with other apps.

## Practical limits

* Heavy extension workflows that expect a desktop (remote containers, some debug adapters with local UIs, AI assistants distributed only through the Microsoft Marketplace) may not work. Server-side equivalents often do.
* Large screenshots, drag-and-drop between Android apps and the file tree are clumsy; use the editor's Upload/Download context-menu items, or `scp`/`rsync` from a terminal.
* File pickers and the clipboard are subject to Android's browser permissions; allow clipboard access for the site if prompted.
* A phone works for reviewing and small fixes; a tablet with a keyboard is where this is pleasant.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Blank workbench, spinner forever | a proxy is buffering or rewriting the websocket (check `flush_interval -1`, and that Cloudflare Rocket Loader is not rewriting scripts: `no-transform`) |
| 502 from Warpgate | the forwarder is down or the target URL is wrong: `docker ps`, `docker logs vscode` |
| Forwarder logs `forward to … failed` | code-server is not running or the socket path/permissions are wrong: `systemctl status code-server`, `ls -l ~/.local/run/code-server` |
| Keeps asking you to sign in | Warpgate session timeout, or cookies blocked: allow cookies for the bastion hostname |
| Shortcut does nothing | the browser/OS took it; use the command palette or the installed app |
| Extension not found | it is not on Open VSX; look for the publisher's Open VSX listing or install a `.vsix` |
| Cannot paste | the page is not a secure context, or the browser blocked clipboard access for the site |
