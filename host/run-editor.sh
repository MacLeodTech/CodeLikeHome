#!/bin/sh
# Starts code-server for one user. Installed as /usr/local/lib/codelikehome/run-editor and run by code-server@USER.service as root,
# which drops to USER before the editor starts: that user's uid and gid, the groups and umask of the installed profile, their
# home directory and shell. Usage: run-editor USER
set -eu
user=${1:-}
case $user in ''|-*|*[!a-z0-9_-]*) echo "run-editor: bad user name" >&2; exit 2;; esac
grep -qx "$user" /etc/codelikehome/gate.users || { echo "run-editor: $user is not in /etc/codelikehome/gate.users" >&2; exit 2; }
entry=$(getent passwd "$user") || { echo "run-editor: no such user $user" >&2; exit 2; }
uid=$(echo "$entry" | cut -d: -f3); gid=$(echo "$entry" | cut -d: -f4); home=$(echo "$entry" | cut -d: -f6); shell=$(echo "$entry" | cut -d: -f7)
[ "$uid" -ge 1000 ] || { echo "run-editor: refusing uid $uid" >&2; exit 2; }
prof() { sed -n "s/^$1=//p" /etc/codelikehome/profile | head -n1; }
groups=$(prof groups); umask_=$(prof umask); workspace=$(prof workspace)
case $groups in
  login) groupflag="--init-groups";;
  none|"") groupflag="--clear-groups";;
  *) groupflag="--groups=$groups";;
esac
bin=""
for c in "$home/.local/bin/code-server" /usr/local/bin/code-server /usr/bin/code-server; do [ -x "$c" ] && { bin=$c; break; }; done
[ -n "$bin" ] || { echo "run-editor: code-server is not installed for $user (see docs/07-multi-user-login.md)" >&2; exit 1; }
[ -n "$workspace" ] || { [ -d "$home/projects" ] && workspace="$home/projects" || workspace=$home; }

# per-user runtime directory: only that user may enter it, so only they (and the gate, as root) can reach the socket
install -d -m 755 /run/codelikehome /run/codelikehome/users
rundir=/run/codelikehome/users/$uid
install -d -m 700 -o "$uid" -g "$gid" "$rundir"
rm -f "$rundir/code-server.sock"
umask 077
cat > "$rundir/config.yaml" <<CFG
socket: $rundir/code-server.sock
socket-mode: 600
auth: none
cert: false
disable-telemetry: true
CFG
chown "$uid:$gid" "$rundir/config.yaml"
umask "${umask_:-0077}"
cd "$home"
exec setpriv --reuid="$uid" --regid="$gid" $groupflag env \
  HOME="$home" USER="$user" LOGNAME="$user" SHELL="$shell" PATH="$home/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  "$bin" --config "$rundir/config.yaml" "$workspace"
