#!/bin/sh
# Installs the host half of CodeLikeHome. Safe to re-run: it only rewrites what differs and restarts the editor only if its unit changed.
#   sudo sh host/install.sh --user NAME [--profile strict|balanced|workstation] [--workspace DIR] [--dry-run]
#        [--groups none|login|g1,g2] [--umask 0077] [--env none|allowlist|all] [--vars "A B"] [--sudo-timeout MIN]
#        [--gate [--users a,b,c]]
#
# --gate swaps the single always-on editor for a sign-in page (docs/07-multi-user-login.md): codelikehome-gate checks a username and
# password with PAM and starts code-server@USER, which runs as that user with the profile's groups. --user is then only the owner of
# the socket the forwarder opens; --users lists who may sign in (default: just --user).
#
# Profiles set the defaults below; any option given explicitly overrides its profile value.
#   profile      groups         umask  env        sudo cache
#   strict       none           0077   allowlist   5 min   (default; the editor cannot reach Docker, files are private)
#   balanced     none (+ --groups)  0027  allowlist  15 min  (add only the groups you need, e.g. --groups docker)
#   workstation  login          0022   all        60 min   (the editor acts as you: root-equivalent if you are in docker)
#
# What it does:
#   1. creates ~NAME/.local/run/code-server (mode 700) and ~NAME/.config/code-server/config.yaml (socket, mode 600)
#   2. installs /etc/systemd/system/code-server.service: runs the editor as NAME with the profile's groups and umask
#   3. writes /etc/code-server/environment (the variables the editor's terminals inherit; see --env)
#   4. sets the sudo password cache (/etc/sudoers.d/90-codelikehome-timeout)
#   5. installs the verification script and an hourly root timer that runs it
#   6. records the installed version and the chosen profile (/etc/codelikehome/installed-version, /etc/codelikehome/profile)
# It does not install code-server itself or touch Docker. Read it first; it is short.
set -eu
here=$(cd "$(dirname "$0")" && pwd); version=$(cat "$here/../VERSION")
user=""; workspace=""; profile=strict; envmode=""; vars="LANG LC_ALL TZ EDITOR"; dry=0; groups=""; umask_=""; sudo_t=""; gate=0; users=""; workspace_given=""
while [ $# -gt 0 ]; do case $1 in
  --user) user=$2; shift 2;; --workspace) workspace=$2; workspace_given=1; shift 2;; --env) envmode=$2; shift 2;; --profile) profile=$2; shift 2;;
  --groups) groups=$2; shift 2;; --umask) umask_=$2; shift 2;; --sudo-timeout) sudo_t=$2; shift 2;;
  --vars) vars=$2; shift 2;; --gate) gate=1; shift;; --users) users=$2; shift 2;; --dry-run) dry=1; shift;; *) echo "unknown option $1" >&2; exit 2;; esac; done
# profile defaults, then explicit options win
case $profile in
  strict)      d_groups=none;  d_umask=0077; d_env=allowlist; d_sudo=5;;
  balanced)    d_groups=none;  d_umask=0027; d_env=allowlist; d_sudo=15;;
  workstation) d_groups=login; d_umask=0022; d_env=all;       d_sudo=60;;
  *) echo "--profile must be strict, balanced or workstation" >&2; exit 2;; esac
[ -n "$groups" ] || groups=$d_groups; [ -n "$umask_" ] || umask_=$d_umask; [ -n "$envmode" ] || envmode=$d_env; [ -n "$sudo_t" ] || sudo_t=$d_sudo
case $umask_ in [0-7][0-7][0-7]|0[0-7][0-7][0-7]) ;; *) echo "--umask must be octal, like 0077 or 022" >&2; exit 2;; esac
case $sudo_t in ''|*[!0-9]*) echo "--sudo-timeout must be a whole number of minutes" >&2; exit 2;; esac
[ -n "$user" ] || { echo "--user NAME is required" >&2; exit 2; }
[ "$user" != root ] || { echo "refusing to run the editor as root" >&2; exit 2; }
[ "$dry" = 1 ] || [ "$(id -u)" = 0 ] || { echo "run as root (or add --dry-run)" >&2; exit 1; }
getent passwd "$user" >/dev/null || { echo "no such user: $user" >&2; exit 1; }
uid=$(id -u "$user"); gid=$(id -g "$user"); home=$(getent passwd "$user" | cut -d: -f6); shell=$(getent passwd "$user" | cut -d: -f7)
[ -n "$workspace" ] || workspace="$home/projects"
case $envmode in none) envfile="";; allowlist|all) envfile="EnvironmentFile=-/etc/code-server/environment";; *) echo "--env must be none, allowlist or all" >&2; exit 2;; esac
case $groups in
  none)  groupflag="--clear-groups";;
  login) groupflag="--init-groups";;
  *) for g in $(echo "$groups" | tr ',' ' '); do getent group "$g" >/dev/null || { echo "no such group: $g" >&2; exit 1; }; done
     groupflag="--groups=$groups";;
esac
# the docker group is root-equivalent; say so whenever the editor will have it
dockerish=0
case $groups in none) ;; login) id -nG "$user" | tr ' ' '\n' | grep -qx docker && dockerish=1;; *) echo ",$groups," | grep -q ',docker,' && dockerish=1;; esac
if [ "$dockerish" = 1 ]; then
  echo "WARNING: the editor will be in the docker group. Anyone in the editor can become root on this host without a password" >&2
  echo "         (docker run -v /:/host ...). The sudo password cache no longer protects anything. See docs/03-security.md." >&2
fi
[ -n "$users" ] || users=$user
case $users in *[!a-z0-9_,-]*) echo "--users takes comma-separated user names, no spaces" >&2; exit 2;; esac
for u in $(echo "$users" | tr ',' ' '); do
  case $u in -*|*[!a-z0-9_-]*) echo "bad user name in --users: $u" >&2; exit 2;; esac
  getent passwd "$u" >/dev/null || { echo "no such user: $u" >&2; exit 1; }
  [ "$(id -u "$u")" -ge 1000 ] || { echo "refusing $u: uid below 1000" >&2; exit 2; }
done
if [ "$gate" = 1 ]; then
  [ -f /etc/pam.d/common-auth ] || { echo "--gate needs Debian-style PAM (/etc/pam.d/common-auth); write your own /etc/pam.d/codelikehome" >&2; exit 1; }
  command -v setpriv >/dev/null 2>&1 || { echo "setpriv (util-linux) is required" >&2; exit 1; }
  [ "$dry" = 1 ] || command -v python3 >/dev/null 2>&1 || { echo "python3 is required for --gate" >&2; exit 1; }
  echo "NOTE: --gate runs a root service that checks passwords. Anyone who passes the bastion AND knows a listed user's password gets that user's editor." >&2
  systemctl is-active --quiet code-server 2>/dev/null && echo "NOTE: the old code-server.service is running; it is stopped and disabled so the gate can take over its socket (open editor sessions end)." >&2
fi
[ "$gate" = 1 ] || [ -x "$home/.local/bin/code-server" ] || echo "warning: $home/.local/bin/code-server not found; install code-server for $user first" >&2

changed=""
# put SRC (a file) at DEST with MODE and OWNER if it differs
put() { src=$1; dest=$2; mode=$3; owner=${4:-root:root}
  if [ -f "$dest" ] && cmp -s "$src" "$dest" && [ "$(stat -c %a "$dest")" = "$mode" ]; then echo "same     $dest"; return 1; fi
  if [ "$dry" = 1 ]; then echo "would write $dest ($mode $owner)"; return 0; fi
  mkdir -p "$(dirname "$dest")"; install -m "$mode" -o "${owner%:*}" -g "${owner#*:}" "$src" "$dest"; echo "wrote    $dest"; return 0; }
sub() { sed -e "s|@VERSION@|$version|g" -e "s|@USER@|$user|g" -e "s|@UID@|$uid|g" -e "s|@GID@|$gid|g" -e "s|@HOME@|$home|g" \
  -e "s|@WORKSPACE@|$workspace|g" -e "s|@SHELL@|$shell|g" -e "s|@SOCKET@|$home/.local/run/code-server/code-server.sock|g" -e "s|@ENVFILE@|$envfile|g" \
  -e "s|@PROFILE@|$profile|g" -e "s|@GROUPFLAG@|$groupflag|g" -e "s|@UMASK@|$umask_|g" "$1"; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

# 1. socket directory and editor config (owned by the user)
rundir="$home/.local/run/code-server"
if [ "$dry" = 1 ]; then echo "would ensure $rundir (700 $user)"; else mkdir -p "$rundir" "$home/.config/code-server"; chown "$user:" "$rundir"; chmod 700 "$rundir"; fi
if [ "$gate" = 0 ]; then
  sub "$here/config.yaml.in" > "$tmp/config.yaml"
  put "$tmp/config.yaml" "$home/.config/code-server/config.yaml" 600 "$user:$gid" || true

  # 2. the unit
  sub "$here/code-server.service.in" > "$tmp/code-server.service"
  if put "$tmp/code-server.service" /etc/systemd/system/code-server.service 644; then changed=unit; fi
else
  # 2g. the gate: per-user editor template, run-editor, the gate itself, its PAM service, who may sign in, and its unit
  sub "$here/code-server@.service.in" > "$tmp/ce.service"
  put "$tmp/ce.service" /etc/systemd/system/code-server@.service 644 && changed="$changed template" || true
  put "$here/run-editor.sh" /usr/local/lib/codelikehome/run-editor 755 || true
  put "$here/gate.py" /usr/local/lib/codelikehome/gate.py 755 && changed="$changed gcode" || true
  put "$here/pam-codelikehome" /etc/pam.d/codelikehome 644 || true
  echo "$users" | tr ',' '\n' > "$tmp/gate.users"
  put "$tmp/gate.users" /etc/codelikehome/gate.users 644 || true
  printf 'GATE_SOCKET=%s\nGATE_SOCKET_UID=%s\nGATE_SOCKET_GID=%s\nGATE_PAM_SERVICE=codelikehome\nGATE_USERS_FILE=/etc/codelikehome/gate.users\n' \
    "$home/.local/run/code-server/code-server.sock" "$uid" "$gid" > "$tmp/gate.env"
  put "$tmp/gate.env" /etc/codelikehome/gate.env 644 && changed="$changed genv" || true
  sub "$here/codelikehome-gate.service.in" | sed "s|@RUNDIR@|$rundir|g" > "$tmp/gate.service"
  put "$tmp/gate.service" /etc/systemd/system/codelikehome-gate.service 644 && changed="$changed gsvc" || true
fi

# 3. environment for the editor's terminals
case $envmode in
  allowlist) : > "$tmp/env"; for v in $vars; do eval "val=\${$v:-}"; [ -n "$val" ] || val=$(grep -s "^$v=" /etc/environment | head -n1 | cut -d= -f2- | tr -d '"') || true
                             [ -n "$val" ] && printf '%s=%s\n' "$v" "$val" >> "$tmp/env"; done
             put "$tmp/env" /etc/code-server/environment 644 || true;;
  all) grep -s '^[A-Za-z_][A-Za-z0-9_]*=' /etc/environment > "$tmp/env" || true
       echo "note: --env all copies EVERY variable in /etc/environment into the editor's terminals and extensions, secrets included" >&2
       put "$tmp/env" /etc/code-server/environment 600 || true;;
esac

# 4. sudo password cache
for u in $(echo "$users" | tr ',' ' '); do printf 'Defaults:%s timestamp_timeout=%s\n' "$u" "$sudo_t"; done > "$tmp/sudoers"
if command -v visudo >/dev/null 2>&1 && ! visudo -cf "$tmp/sudoers" >/dev/null; then echo "sudoers snippet failed validation" >&2; exit 1; fi
put "$tmp/sudoers" /etc/sudoers.d/90-codelikehome-timeout 440 || true

# 5. verification script and hourly timer
put "$here/verify.sh" /usr/local/lib/codelikehome/verify.sh 755 || true
sub "$here/codelikehome-verify.service.in" > "$tmp/v.service"
put "$tmp/v.service" /etc/systemd/system/codelikehome-verify.service 644 && changed="$changed timer" || true
put "$here/codelikehome-verify.timer" /etc/systemd/system/codelikehome-verify.timer 644 && changed="$changed timer" || true

# 6. record the version
printf '%s\n' "$version" > "$tmp/version"; put "$tmp/version" /etc/codelikehome/installed-version 644 || true
# the chosen profile, read by verify.sh so it checks against what you asked for rather than the strictest setting
printf 'profile=%s\ngroups=%s\numask=%s\nsudo_timeout=%s\ngate=%s\nusers=%s\n' "$profile" "$groups" "$umask_" "$sudo_t" "$gate" "$users" > "$tmp/profile"
[ "$gate" = 0 ] || [ -z "$workspace_given" ] || printf 'workspace=%s\n' "$workspace" >> "$tmp/profile"
put "$tmp/profile" /etc/codelikehome/profile 644 || true

if [ "$dry" = 1 ]; then echo "dry run: nothing changed"; exit 0; fi
if [ -n "$changed" ]; then
  systemctl daemon-reload
  systemctl enable --now codelikehome-verify.timer >/dev/null 2>&1 || true
  case $changed in *unit*) systemctl enable code-server >/dev/null 2>&1 || true; systemctl restart code-server; echo "restarted code-server (open editor sessions reconnect)";; esac
  case $changed in *gsvc*|*gcode*|*genv*)
    systemctl disable --now code-server >/dev/null 2>&1 || true          # the gate takes over the old editor's socket path
    systemctl enable codelikehome-gate >/dev/null 2>&1 || true; systemctl restart codelikehome-gate
    echo "started codelikehome-gate; users sign in at the editor's address: $users";; esac
fi
echo "CodeLikeHome host files $version installed. Next: start the forwarder (docs/02-setup.md) and run: sudo $here/verify.sh --user $user"
