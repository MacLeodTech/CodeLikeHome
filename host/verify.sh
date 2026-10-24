#!/bin/sh
# Read-only checks that the editor is reachable only through the bastion.
# Run as root (the hourly timer does) or as the editor user (some checks then say "skipped").
#   verify.sh [--user NAME] [--container vscode] [--bastion warpgate] [--network vscode_net] [--notify-on-failure]
# --notify-on-failure runs the command in /etc/codelikehome/notify (if present) with a one-line message when a check fails.
user=${SUDO_USER:-$(id -un)}; f=vscode; w=warpgate; n=vscode_net; notify=0
while [ $# -gt 0 ]; do case $1 in
  --user) user=$2; shift 2;; --container) f=$2; shift 2;; --bastion) w=$2; shift 2;; --network) n=$2; shift 2;;
  --notify-on-failure) notify=1; shift;; *) echo "unknown option $1" >&2; exit 2;; esac; done
ok() { printf 'ok    %s\n' "$1"; }; bad() { printf 'FAIL  %s\n' "$1"; fails="$fails; $1"; rc=1; }; skip() { printf 'skip  %s\n' "$1"; }
rc=0; fails=""
home=$(getent passwd "$user" | cut -d: -f6); uid=$(id -u "$user")
sock="$home/.local/run/code-server/code-server.sock"
[ "$(stat -c %a "$sock" 2>/dev/null)" = 600 ] && ok "socket is mode 600" || bad "socket missing or not mode 600"
[ "$(stat -c %U "$sock" 2>/dev/null)" = "$user" ] && ok "socket is owned by $user" || bad "socket is not owned by $user"
[ "$(stat -c %a "$(dirname "$sock")" 2>/dev/null)" = 700 ] && ok "socket directory is mode 700" || bad "socket directory is not mode 700"
ss -tlnp 2>/dev/null | grep -qi code-server && bad "code-server has a TCP listener" || ok "code-server has no TCP listener"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  [ "$(docker network inspect "$n" --format '{{.Internal}}' 2>/dev/null)" = true ] && ok "$n is internal" || bad "$n is not internal"
  members=$(docker network inspect "$n" --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null | tr ' ' '\n' | sort | grep . | tr '\n' ' ')
  want=$(printf '%s\n%s\n' "$f" "$w" | sort | tr '\n' ' ')
  [ "$members" = "$want" ] && ok "$n members: $members" || bad "$n members are '$members', expected '$want'"
  [ -z "$(docker port "$f" 2>/dev/null)" ] && ok "$f publishes no ports" || bad "$f publishes a port"
  docker exec "$f" printenv FORWARD_ALLOW 2>/dev/null | grep -q . && ok "$f has FORWARD_ALLOW set" || bad "$f has no FORWARD_ALLOW (any member of $n may connect)"
else skip "docker not usable by this user: network and container checks"; fi
# The check that matters is the EDITOR's groups, not a login shell's (a login shell normally has docker; the unit clears it).
dgid=$(getent group docker | cut -d: -f3); pids=$(pgrep -u "$uid" -f '/code-server-[0-9.]*/lib/node' 2>/dev/null)
if [ -z "$pids" ]; then bad "no code-server process found"; else
  extra=0; for p in $pids; do g=$(awk '/^Groups:/{$1="";print}' /proc/$p/status 2>/dev/null); case " $g " in *" $dgid "*) extra=1;; esac; done
  [ $extra = 0 ] && ok "code-server processes are not in the docker group" || bad "a code-server process is in the docker group"
fi
if [ "$(id -u)" = 0 ]; then
  t=$(cat /etc/sudoers.d/90-codelikehome-timeout 2>/dev/null | sed -n 's/.*timestamp_timeout=\([0-9]*\).*/\1/p')
  [ -n "$t" ] && [ "$t" -le 5 ] && ok "sudo password cache is $t minutes" || bad "sudo password cache is not set to 5 minutes or less"
else skip "sudo cache check needs root"; fi
if [ $rc != 0 ] && [ $notify = 1 ] && [ -x /etc/codelikehome/notify ]; then /etc/codelikehome/notify "CodeLikeHome check failed on $(hostname):${fails}" || true; fi
exit $rc
