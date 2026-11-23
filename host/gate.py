#!/usr/bin/env python3
"""codelikehome-gate: a login page in front of per-user editors.

Runs as root on the host (PAM needs that to check other users' passwords). It listens on the Unix socket the forwarder
container already connects to, shows a small sign-in form, checks the password with PAM, starts `code-server@USER` (a systemd
template unit that drops to that user's uid, groups and home) and then proxies the browser to that user's own mode-600 socket.

Every request is authenticated on its own: one client connection carries exactly one request (the editor is told
`Connection: close`), and only a WebSocket upgrade, which the protocol makes a single long-lived stream, is relayed raw.
That keeps a pooled or pipelined connection from smuggling a second request past the cookie check.

Standard library only. Configuration (environment, written by host/install.sh into /etc/codelikehome/gate.env):
  GATE_SOCKET          Unix socket to listen on (the forwarder's FORWARD_UNIX)         required
  GATE_SOCKET_UID/GID  owner of that socket (the forwarder's uid); mode is 600          required
  GATE_USERS_FILE      allowed users, one per line, '#' comments   (/etc/codelikehome/gate.users)
  GATE_RUN_DIR         per-uid editor sockets live in RUN_DIR/<uid>/code-server.sock (/run/codelikehome/users)
  GATE_PAM_SERVICE     PAM service name                             (codelikehome)
  GATE_MIN_UID         refuse uids below this                       (1000)
  GATE_SESSION_HOURS   absolute session lifetime                    (12)
  GATE_IDLE_MINUTES    session expires after this much silence      (120)
  GATE_COOKIE_SECURE   "0" to drop the Secure cookie flag (plain-HTTP testing only)   (1)
"""

import ctypes
import ctypes.util
import html
import os
import pwd
import re
import secrets
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse

PREFIX = "/__clh/"
SESSION_COOKIE = "clh_session"
CSRF_COOKIE = "clh_csrf"
NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
MAX_HEAD = 16384
MAX_FORM = 4096
HOP_BY_HOP = {"connection", "keep-alive", "proxy-connection", "te", "trailer", "upgrade"}


# ---------------------------------------------------------------- PAM (ctypes, no third-party module)
class _Msg(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class _Resp(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


_CONV = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(_Msg)),
                         ctypes.POINTER(ctypes.POINTER(_Resp)), ctypes.c_void_p)


class _PamConv(ctypes.Structure):
    _fields_ = [("conv", _CONV), ("appdata_ptr", ctypes.c_void_p)]


def pam_check(service, user, password):
    """True when PAM both authenticates `user` with `password` and says the account is valid (not expired or locked)."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    libpam = ctypes.CDLL(ctypes.util.find_library("pam") or "libpam.so.0")
    libc.calloc.restype = ctypes.c_void_p
    libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
    libc.strdup.restype = ctypes.c_void_p
    libc.strdup.argtypes = [ctypes.c_char_p]
    libpam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(_PamConv), ctypes.POINTER(ctypes.c_void_p)]
    libpam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
    libpam.pam_acct_mgmt.argtypes = [ctypes.c_void_p, ctypes.c_int]
    libpam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
    secret = password.encode()

    def converse(count, msgs, resp, _data):
        arr = libc.calloc(count, ctypes.sizeof(_Resp))
        if not arr:
            return 5                                         # PAM_BUF_ERR
        out = ctypes.cast(arr, ctypes.POINTER(_Resp))
        for i in range(count):
            if msgs[i].contents.msg_style in (1, 2):         # PAM_PROMPT_ECHO_OFF / _ON: answer with the password
                out[i].resp = libc.strdup(secret)
        resp[0] = out
        return 0

    conv = _PamConv(_CONV(converse), None)                  # keep a reference for the whole call
    handle = ctypes.c_void_p()
    if libpam.pam_start(service.encode(), user.encode(), ctypes.byref(conv), ctypes.byref(handle)) != 0:
        return False
    rc = libpam.pam_authenticate(handle, 0)
    if rc == 0:
        rc = libpam.pam_acct_mgmt(handle, 0)
    libpam.pam_end(handle, rc)
    return rc == 0


# ---------------------------------------------------------------- the pages
_STYLE = ("body{font:16px system-ui,sans-serif;background:#14161a;color:#e6e6e6;display:grid;place-items:center;min-height:100vh;margin:0}"
          "main{width:min(22rem,90vw)}h1{font-size:1.25rem;margin:0 0 1rem}label{display:block;margin:.75rem 0 .25rem}"
          "input{width:100%;box-sizing:border-box;padding:.6rem;font:inherit;border-radius:6px;border:1px solid #444;background:#1f2228;color:inherit}"
          "button{margin-top:1rem;width:100%;padding:.65rem;font:inherit;border:0;border-radius:6px;background:#3b82f6;color:#fff;cursor:pointer}"
          "button.alt{background:#2b2f37}.err{color:#ff8a80;margin:.5rem 0}.note{color:#9aa0a6;font-size:.9rem}a{color:#7aa7ff}")


def page(title, body):
    return (f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{_STYLE}</style><main>{body}</main></html>").encode()


def login_page(csrf, nxt, error="", signed_in_as=""):
    msg = f"<p class=err role=alert>{html.escape(error)}</p>" if error else ""
    who = ""
    if signed_in_as:
        who = (f"<p class=note>Signed in as <b>{html.escape(signed_in_as)}</b>. <a href='{html.escape(nxt, quote=True)}'>Open the editor</a>"
               f" or sign in as someone else below.</p>")
    return page("Sign in", f"<h1>Sign in to your editor</h1>{who}{msg}<form method=post action='{PREFIX}login'>"
                f"<input type=hidden name=csrf value='{html.escape(csrf, quote=True)}'><input type=hidden name=next value='{html.escape(nxt, quote=True)}'>"
                "<label for=u>Username</label><input id=u name=username autocomplete=username autocapitalize=none autofocus required maxlength=32>"
                "<label for=p>Password</label><input id=p name=password type=password autocomplete=current-password required maxlength=512>"
                "<button>Sign in</button></form>"
                + (f"<form method=post action='{PREFIX}logout'><button class=alt>Sign out</button></form>" if signed_in_as else ""))


# ---------------------------------------------------------------- HTTP parsing
class BadRequest(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status, self.text = status, text


def read_head(conn, timeout=30):
    """Read up to the blank line that ends the request head. Returns (head_bytes, leftover_bytes) or None on EOF."""
    conn.settimeout(timeout)
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > MAX_HEAD:
            raise BadRequest(431, "request head too large")
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    if len(head) > MAX_HEAD:
        raise BadRequest(431, "request head too large")
    return head, rest


def parse_head(head):
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise BadRequest(400, "bad request line")
    headers = []
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if not sep or not name or name != name.strip() or line[0] in " \t":
            raise BadRequest(400, "bad header")
        headers.append((name, value.strip()))
    return parts[0], parts[1], parts[2], headers


def header_values(headers, name):
    return [v for k, v in headers if k.lower() == name]


def parse_cookies(headers):
    jar = {}
    for value in header_values(headers, "cookie"):
        for item in value.split(";"):
            k, _, v = item.strip().partition("=")
            if k:
                jar.setdefault(k, v)
    return jar


def strip_cookie(value, names):
    kept = [item.strip() for item in value.split(";") if item.strip().partition("=")[0] not in names]
    return "; ".join(kept)


def is_websocket(headers):
    conn = ",".join(header_values(headers, "connection")).lower()
    return "upgrade" in conn and any(v.lower() == "websocket" for v in header_values(headers, "upgrade"))


def body_length(headers, upgrade):
    if header_values(headers, "transfer-encoding"):
        raise BadRequest(501, "chunked request bodies are not supported")
    lengths = {v for v in header_values(headers, "content-length")}
    if not lengths:
        return 0
    if len(lengths) != 1:
        raise BadRequest(400, "conflicting Content-Length")
    value = lengths.pop()
    if not value.isdigit() or int(value) > 1 << 31 or (upgrade and int(value)):
        raise BadRequest(400, "bad Content-Length")
    return int(value)


def response(status, reason, body=b"", headers=()):
    head = [f"HTTP/1.1 {status} {reason}", f"Content-Length: {len(body)}", "Connection: close", "Cache-Control: no-store",
            "X-Frame-Options: DENY", "X-Content-Type-Options: nosniff", "Referrer-Policy: no-referrer"]
    if body:
        head.append("Content-Type: text/html; charset=utf-8")
        head.append("Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
    head.extend(headers)
    return ("\r\n".join(head) + "\r\n\r\n").encode() + body


def safe_next(value):
    """Only same-site absolute paths, never our own pages or a protocol-relative URL."""
    if value.startswith("/") and not value.startswith("//") and not value.startswith(PREFIX) and "\\" not in value and "\n" not in value:
        return value
    return "/"


# ---------------------------------------------------------------- the gate
class Gate:
    def __init__(self, env=None, authenticate=None, start_editor=None):
        env = os.environ if env is None else env
        self.socket_path = env.get("GATE_SOCKET", "")
        self.socket_uid = int(env.get("GATE_SOCKET_UID", "-1"))
        self.socket_gid = int(env.get("GATE_SOCKET_GID", "-1"))
        self.users_file = env.get("GATE_USERS_FILE", "/etc/codelikehome/gate.users")
        self.run_dir = env.get("GATE_RUN_DIR", "/run/codelikehome/users")
        self.pam_service = env.get("GATE_PAM_SERVICE", "codelikehome")
        self.min_uid = int(env.get("GATE_MIN_UID", "1000"))
        self.session_secs = float(env.get("GATE_SESSION_HOURS", "12")) * 3600
        self.idle_secs = float(env.get("GATE_IDLE_MINUTES", "120")) * 60
        self.cookie_secure = env.get("GATE_COOKIE_SECURE", "1") != "0"
        self.max_conn = int(env.get("GATE_MAX", "128"))
        self.authenticate = authenticate or (lambda user, pw: pam_check(self.pam_service, user, pw))
        self.start_editor = start_editor or self._systemctl_start
        self.sessions = {}                                   # token -> {user, uid, born, seen}
        self.failures = {}                                   # user -> [timestamps]
        self.global_failures = []
        self.lock = threading.Lock()
        self.editor_locks = {}
        self.slots = threading.BoundedSemaphore(self.max_conn)

    # --- who may sign in
    def allowed_users(self):
        try:
            with open(self.users_file, encoding="utf-8") as f:
                return {line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")}
        except OSError:
            return set()

    def lookup(self, user):
        """The passwd entry when `user` may use the editor at all, else None."""
        if not NAME_RE.match(user or "") or user not in self.allowed_users():
            return None
        try:
            entry = pwd.getpwnam(user)
        except KeyError:
            return None
        if entry.pw_uid < self.min_uid or entry.pw_shell.endswith(("nologin", "false")):
            return None
        return entry

    # --- throttling: 5 bad passwords per user or 30 overall in 10 minutes pauses sign-in for that user / for everyone
    def throttled(self, user):
        now = time.time()
        with self.lock:
            mine = self.failures[user] = [t for t in self.failures.get(user, []) if now - t < 600]
            self.global_failures = [t for t in self.global_failures if now - t < 600]
            return len(mine) >= 5 or len(self.global_failures) >= 30

    def note_failure(self, user):
        with self.lock:
            now = time.time()
            self.failures.setdefault(user, []).append(now)
            self.global_failures.append(now)

    # --- sessions
    def new_session(self, user, uid):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.lock:
            self.sessions[token] = {"user": user, "uid": uid, "born": now, "seen": now}
        return token

    def session(self, headers):
        token = parse_cookies(headers).get(SESSION_COOKIE, "")
        now = time.time()
        with self.lock:
            for k in [k for k, s in self.sessions.items() if now - s["born"] > self.session_secs or now - s["seen"] > self.idle_secs]:
                del self.sessions[k]
            sess = self.sessions.get(token)
            if sess is None:
                return None
            sess["seen"] = now
            sess = dict(sess, token=token)
        entry = self.lookup(sess["user"])                    # removed from the users file, or account changed: session is void
        if entry is None or entry.pw_uid != sess["uid"]:
            self.drop(token)
            return None
        return sess

    def drop(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    # --- the user's editor
    def editor_socket(self, uid):
        return os.path.join(self.run_dir, str(uid), "code-server.sock")

    def _systemctl_start(self, user):
        subprocess.run(["systemctl", "start", f"code-server@{user}.service"], check=False, timeout=30, capture_output=True)

    def _can_connect(self, path):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        try:
            s.connect(path)
            return True
        except OSError:
            return False
        finally:
            s.close()

    def ensure_editor(self, user, uid, wait=30):
        path = self.editor_socket(uid)
        if self._can_connect(path):
            return path
        with self.lock:
            lk = self.editor_locks.setdefault(user, threading.Lock())
        with lk:
            if self._can_connect(path):
                return path
            log(f"starting editor for {user}")
            self.start_editor(user)
            deadline = time.time() + wait
            while time.time() < deadline:
                if self._can_connect(path):
                    return path
                time.sleep(0.3)
        return None

    # --- request handling
    def serve(self, conn, peer=""):
        if not self.slots.acquire(blocking=False):
            conn.close()
            return
        try:
            try:
                got = read_head(conn)
                if got is None:
                    return
                head, rest = got
                method, target, _version, headers = parse_head(head)
                self.dispatch(conn, method, target, headers, rest)
            except BadRequest as e:
                conn.sendall(response(e.status, "Error", page("Error", f"<h1>{html.escape(e.text)}</h1>")))
            except (OSError, ValueError):
                pass
        finally:
            self.slots.release()
            try:
                conn.close()
            except OSError:
                pass

    def dispatch(self, conn, method, target, headers, rest):
        path, _, query = target.partition("?")
        sess = self.session(headers)
        if path == PREFIX + "login":
            return self.login(conn, method, query, headers, rest, sess)
        if path == PREFIX + "logout":
            if method != "POST":
                return conn.sendall(response(405, "Method Not Allowed"))
            if sess:
                self.drop(sess["token"])
                log(f"{sess['user']} signed out")
            return conn.sendall(response(303, "See Other", headers=[f"Location: {PREFIX}login", self.cookie(SESSION_COOKIE, "", 0)]))
        if path.startswith(PREFIX):
            return conn.sendall(response(404, "Not Found"))
        if sess is None:
            wants_page = method in ("GET", "HEAD") and not is_websocket(headers) and any("text/html" in v for v in header_values(headers, "accept"))
            if wants_page:
                return conn.sendall(response(303, "See Other", headers=[f"Location: {PREFIX}login?next={urllib.parse.quote(target, safe='')}"]))
            return conn.sendall(response(401, "Unauthorized", page("Sign in", f"<h1>Sign in required</h1><p><a href='{PREFIX}login'>Sign in</a></p>")))
        self.proxy(conn, method, target, headers, rest, sess)

    def cookie(self, name, value, max_age, same_site="Lax"):
        return f"Set-Cookie: {name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite={same_site}" + ("; Secure" if self.cookie_secure else "")

    def login(self, conn, method, query, headers, rest, sess):
        jar = parse_cookies(headers)
        csrf = jar.get(CSRF_COOKIE, "")
        fresh = []
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", csrf):
            csrf = secrets.token_urlsafe(24)
            fresh = [self.cookie(CSRF_COOKIE, csrf, 3600, "Strict")]
        nxt = safe_next(urllib.parse.parse_qs(query).get("next", ["/"])[0])
        if method in ("GET", "HEAD"):
            return conn.sendall(response(200, "OK", login_page(csrf, nxt, signed_in_as=sess["user"] if sess else ""), fresh))
        if method != "POST":
            return conn.sendall(response(405, "Method Not Allowed"))
        length = body_length(headers, False)
        if length > MAX_FORM:
            raise BadRequest(413, "form too large")
        body = rest
        conn.settimeout(10)
        while len(body) < length:
            chunk = conn.recv(length - len(body))
            if not chunk:
                return
            body += chunk
        form = {k: v[0] for k, v in urllib.parse.parse_qs(body[:length].decode("utf-8", "replace"), keep_blank_values=True).items()}
        nxt = safe_next(form.get("next", "/"))

        def again(error, status=200):
            return conn.sendall(response(status, "OK" if status == 200 else "Error", login_page(csrf, nxt, error), fresh))

        user, password = form.get("username", "").strip(), form.get("password", "")
        if fresh or not secrets.compare_digest(form.get("csrf", "").encode(), csrf.encode()):   # no cookie yet, or it does not match the form
            return again("The form expired. Try again.", 403)
        if "\x00" in password or not user:
            return again("Wrong username or password.", 401)
        if self.throttled(user):
            log(f"sign-in paused for {user!r:.40}: too many failures")
            return again("Too many failed attempts. Wait a few minutes.", 429)
        entry = self.lookup(user)
        ok = entry is not None and self.authenticate(user, password)
        if not ok:
            self.note_failure(user)
            time.sleep(1)                                    # slows guessing; unknown and known users cost the same
            log(f"sign-in failed for {user!r:.40}")
            return again("Wrong username or password.", 401)
        with self.lock:
            self.failures.pop(user, None)
        token = self.new_session(user, entry.pw_uid)
        if sess:
            self.drop(sess["token"])
        log(f"{user} signed in")
        try:
            self.ensure_editor(user, entry.pw_uid)           # warm it up so the first page load is not a 502
        except OSError:
            pass
        return conn.sendall(response(303, "See Other", headers=[f"Location: {nxt}", self.cookie(SESSION_COOKIE, token, int(self.session_secs))]))

    def proxy(self, conn, method, target, headers, rest, sess):
        upgrade = is_websocket(headers)
        remaining = body_length(headers, upgrade)
        sock = self.ensure_editor(sess["user"], sess["uid"], wait=30)
        if sock is None:
            return conn.sendall(response(502, "Bad Gateway", page("Editor unavailable", "<h1>Your editor did not start</h1><p class=note>Check <code>journalctl -u code-server@" + html.escape(sess["user"]) + "</code>.</p>")))
        up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            up.settimeout(10)
            up.connect(sock)
            up.settimeout(None)
            out = [f"{method} {target} HTTP/1.1"]
            for name, value in headers:
                low = name.lower()
                if low == "cookie":
                    value = strip_cookie(value, {SESSION_COOKIE, CSRF_COOKIE})
                    if not value:
                        continue
                elif low in HOP_BY_HOP or low == "content-length":
                    continue
                out.append(f"{name}: {value}")
            if remaining:
                out.append(f"Content-Length: {remaining}")
            out.append("Connection: Upgrade\r\nUpgrade: websocket" if upgrade else "Connection: close")
            up.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
            first = rest if upgrade else rest[:remaining]    # whatever follows the body is a pipelined request: never forwarded
            if first:
                up.sendall(first)
            self.pump(conn, up, remaining - len(first), upgrade)
        finally:
            up.close()

    def pump(self, client, up, body_left, upgrade, idle=1800):
        """Client -> editor for the declared body only (everything for a WebSocket), editor -> client until it closes."""
        client.settimeout(None)
        watch = {client, up}
        while True:
            if not upgrade and body_left <= 0:
                watch.discard(client)
            ready, _, _ = select.select(list(watch), [], [], idle)
            if not ready:
                return
            for src in ready:
                data = src.recv(65536)
                if src is client:
                    if not data:
                        watch.discard(client)
                        if upgrade:
                            return
                        continue
                    if not upgrade:
                        data = data[:body_left]
                        body_left -= len(data)
                    up.sendall(data)
                else:
                    if not data:
                        return
                    client.sendall(data)


def log(msg):
    print(msg, flush=True)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, gate):
        self.gate = gate
        path = gate.socket_path
        if os.path.exists(path):
            os.unlink(path)
        old = os.umask(0o177)
        try:
            super().__init__(path, _Handler)
        finally:
            os.umask(old)
        if gate.socket_uid >= 0:
            os.chown(path, gate.socket_uid, gate.socket_gid)
        os.chmod(path, 0o600)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.gate.serve(self.request)


def main():
    gate = Gate()
    if not gate.socket_path:
        sys.exit("GATE_SOCKET is required")
    os.makedirs(gate.run_dir, mode=0o755, exist_ok=True)
    log(f"codelikehome-gate listening on {gate.socket_path}, users from {gate.users_file}")
    Server(gate).serve_forever()


if __name__ == "__main__":
    main()
