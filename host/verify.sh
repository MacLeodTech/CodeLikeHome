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
# What the installer was asked for (host/install.sh --profile). Without the file, check against the strictest settings.
profile=strict; pgroups=none; pumask=0077; psudo=5
if [ -r /etc/codelikehome/profile ]; then
  for k in profile groups umask sudo_timeout; do v=$(sed -n "s/^$k=//p" /etc/codelikehome/profile | head -n1); [ -n "$v" ] || continue
    case $k in profile) profile=$v;; groups) pgroups=$v;; umask) pumask=$v;; sudo_timeout) psudo=$v;; esac; done
fi
echo "info  profile: $profile (groups=$pgroups umask=$pumask sudo cache=${psudo}m)"
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
# The check that matters is the EDITOR's groups and umask, not a login shell's (a login shell normally has docker; the unit
# sets them per profile). Allowed groups: the primary group plus whatever the profile grants; any other group is drift.
dgid=$(getent group docker | cut -d: -f3); pids=$(pgrep -u "$uid" -f '/code-server-[0-9.]*/lib/node' 2>/dev/null)
gid=$(id -g "$user")
case $pgroups in
  none) allowed=" $gid ";;
  login) allowed=" $gid $(id -G "$user") ";;
  *) allowed=" $gid"; for g in $(echo "$pgroups" | tr ',' ' '); do allowed="$allowed $(getent group "$g" | cut -d: -f3)"; done; allowed="$allowed ";;
esac
if [ -z "$pids" ]; then bad "no code-server process found"; else
  extra=""; hasdocker=0; badumask=""
  for p in $pids; do
    g=$(awk '/^Groups:/{$1="";print}' /proc/$p/status 2>/dev/null)
    for x in $g; do case "$allowed" in *" $x "*) ;; *) extra="$extra $x";; esac; [ "$x" = "$dgid" ] && hasdocker=1; done
    u=$(awk '/^Umask:/{print $2}' /proc/$p/status 2>/dev/null)
    [ -z "$u" ] || [ "$(printf '%o' "0$u")" = "$(printf '%o' "0$pumask")" ] || badumask="$badumask $p:$u"
  done
  [ -z "$extra" ] && ok "code-server groups match the profile ($pgroups)" || bad "code-server has groups the profile does not grant:$extra"
  if [ $hasdocker = 1 ]; then echo "info  code-server is in the docker group (profile $profile): the editor is root-equivalent"; fi
  [ -z "$badumask" ] && ok "code-server umask is $pumask" || bad "code-server umask differs from the profile ($pumask):$badumask"
fi
if [ "$(id -u)" = 0 ]; then
  t=$(cat /etc/sudoers.d/90-codelikehome-timeout 2>/dev/null | sed -n 's/.*timestamp_timeout=\([0-9]*\).*/\1/p')
  [ -n "$t" ] && [ "$t" -le "$psudo" ] && ok "sudo password cache is $t minutes" || bad "sudo password cache is not set to $psudo minutes or less"
else skip "sudo cache check needs root"; fi
if [ $rc != 0 ] && [ $notify = 1 ] && [ -x /etc/codelikehome/notify ]; then /etc/codelikehome/notify "CodeLikeHome check failed on $(hostname):${fails}" || true; fi
exit $rc
