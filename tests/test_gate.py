"""The sign-in gate: login, per-user routing, per-request authentication, and the HTTP smuggling guards. PAM and systemd are faked."""
import os
import socket
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import gate as gatemod

USERS = {"alice": 2001, "bob": 2002, "carol": 2003}
PASSWORDS = {"alice": "pw-a", "bob": "pw-b", "carol": "pw-c"}


class FakeEditor:
    """Stands in for one user's code-server: records what it receives and answers `editor:<uid>`."""

    def __init__(self, path, uid):
        self.uid, self.received = uid, []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(8)
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return conn.close()
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        want = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                want = int(line.split(b":")[1])
        conn.settimeout(0.3)
        try:
            while len(rest) < want:
                rest += conn.recv(4096)
            extra = conn.recv(4096)                          # anything after the declared body would be a smuggled request
        except OSError:
            extra = b""
        self.received.append((head, rest, extra))
        if b"upgrade: websocket" in head.lower():
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            conn.settimeout(2)
            try:
                while chunk := conn.recv(4096):
                    conn.sendall(b"ws:" + chunk)
            except OSError:
                pass
            return conn.close()
        body = f"editor:{self.uid}".encode()
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
        conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "users").write_text("# allowed\nalice\nbob\n")
    run = tmp_path / "run"
    editors = {}
    for uid in USERS.values():
        os.makedirs(run / str(uid))
        editors[uid] = FakeEditor(str(run / str(uid) / "code-server.sock"), uid)
    fake_pwd = types.SimpleNamespace(getpwnam=lambda n: types.SimpleNamespace(pw_uid=USERS[n], pw_shell="/bin/bash") if n in USERS else (_ for _ in ()).throw(KeyError(n)))
    monkeypatch.setattr(gatemod, "pwd", fake_pwd)
    monkeypatch.setattr(gatemod.time, "sleep", lambda s: None)
    started = []
    g = gatemod.Gate(
        env={"GATE_SOCKET": str(tmp_path / "gate.sock"), "GATE_USERS_FILE": str(tmp_path / "users"), "GATE_RUN_DIR": str(run),
             "GATE_MIN_UID": "1000", "GATE_COOKIE_SECURE": "1"},
        authenticate=lambda u, p: PASSWORDS.get(u) == p,
        start_editor=started.append,
    )
    server = gatemod.Server(g)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield types.SimpleNamespace(gate=g, path=g.socket_path, editors=editors, started=started, users_file=tmp_path / "users")
    server.shutdown()
    server.server_close()


def send(env, raw, read_until_close=True):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5)
    c.connect(env.path)
    c.sendall(raw)
    data = b""
    try:
        while chunk := c.recv(65536):
            data += chunk
    except TimeoutError:
        pass
    c.close()
    return data


def req(method, path, headers=(), body=b""):
    lines = [f"{method} {path} HTTP/1.1", "Host: code.example"] + list(headers)
    if body:
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def cookie_of(resp, name):
    for line in resp.decode("latin-1").split("\r\n"):
        if line.lower().startswith("set-cookie: " + name.lower() + "="):
            return line.split(":", 1)[1].split(";")[0].strip().split("=", 1)[1]
    return None


def login(env, user, password, nxt="/"):
    page = send(env, req("GET", "/__clh/login"))
    csrf = cookie_of(page, "clh_csrf")
    form = f"csrf={csrf}&next={nxt}&username={user}&password={password}".encode()
    return send(env, req("POST", "/__clh/login", [f"Cookie: clh_csrf={csrf}", "Content-Type: application/x-www-form-urlencoded"], form))


def signed_in(env, user):
    resp = login(env, user, PASSWORDS[user])
    assert b"303" in resp.split(b"\r\n")[0], resp
    return cookie_of(resp, "clh_session")


def test_html_request_without_session_goes_to_the_login_page(env):
    r = send(env, req("GET", "/some/path?x=1", ["Accept: text/html"]))
    assert r.startswith(b"HTTP/1.1 303")
    assert b"Location: /__clh/login?next=%2Fsome%2Fpath%3Fx%3D1" in r
    assert env.editors[2001].received == []


def test_api_and_websocket_without_session_are_401_and_never_reach_an_editor(env):
    assert send(env, req("POST", "/x", body=b"data")).startswith(b"HTTP/1.1 401")
    ws = req("GET", "/ws", ["Connection: Upgrade", "Upgrade: websocket"])
    assert send(env, ws).startswith(b"HTTP/1.1 401")
    assert all(not e.received for e in env.editors.values())


def test_login_page_sets_csrf_cookie_and_hardening_headers(env):
    r = send(env, req("GET", "/__clh/login"))
    assert r.startswith(b"HTTP/1.1 200")
    assert b"<input id=u name=username" in r and b"type=password" in r
    assert b"X-Frame-Options: DENY" in r and b"no-store" in r
    assert b"HttpOnly" in r and b"SameSite=Strict" in r and b"Secure" in r


def test_wrong_password_unknown_user_and_unlisted_user_are_all_the_same_failure(env):
    for user, pw in (("alice", "nope"), ("mallory", "x"), ("carol", PASSWORDS["carol"])):   # carol exists but is not in the users file
        r = login(env, user, pw)
        assert r.startswith(b"HTTP/1.1 401"), (user, r)
        assert b"Wrong username or password." in r
        assert cookie_of(r, "clh_session") is None


def test_post_without_matching_csrf_is_refused(env):
    form = b"csrf=forged&username=alice&password=pw-a&next=/"
    r = send(env, req("POST", "/__clh/login", ["Cookie: clh_csrf=other-value-other-value"], form))
    assert r.startswith(b"HTTP/1.1 403")
    r = send(env, req("POST", "/__clh/login", [], form))
    assert r.startswith(b"HTTP/1.1 403")


def test_good_login_sets_session_and_proxies_to_that_users_editor(env):
    token = signed_in(env, "alice")
    r = send(env, req("GET", "/app", [f"Cookie: clh_session={token}; theme=dark"]))
    assert r.endswith(b"editor:2001")
    head = env.editors[2001].received[-1][0].decode()
    assert "clh_session" not in head and "theme=dark" in head          # our cookie never reaches the editor
    assert "Connection: close" in head


def test_two_users_reach_two_different_editors(env):
    ta, tb = signed_in(env, "alice"), signed_in(env, "bob")
    assert send(env, req("GET", "/", [f"Cookie: clh_session={ta}"])).endswith(b"editor:2001")
    assert send(env, req("GET", "/", [f"Cookie: clh_session={tb}"])).endswith(b"editor:2002")


def test_switching_user_replaces_the_session(env):
    ta = signed_in(env, "alice")
    page = send(env, req("GET", "/__clh/login", [f"Cookie: clh_session={ta}"]))
    assert b"Signed in as <b>alice</b>" in page
    csrf = cookie_of(send(env, req("GET", "/__clh/login")), "clh_csrf")
    form = f"csrf={csrf}&next=/&username=bob&password=pw-b".encode()
    r = send(env, req("POST", "/__clh/login", [f"Cookie: clh_csrf={csrf}; clh_session={ta}"], form))
    tb = cookie_of(r, "clh_session")
    assert tb and tb != ta
    assert send(env, req("GET", "/", [f"Cookie: clh_session={ta}", "Accept: text/html"])).startswith(b"HTTP/1.1 303")   # alice's token is dead
    assert send(env, req("GET", "/", [f"Cookie: clh_session={tb}"])).endswith(b"editor:2002")


def test_open_redirect_targets_are_ignored(env):
    for bad in ("//evil.example", "https://evil.example", "/__clh/login", "/\\evil"):
        r = login(env, "alice", "pw-a", nxt=bad)
        assert b"Location: /\r\n" in r, (bad, r)


def test_five_failures_pause_that_user_even_with_the_right_password(env):
    for _ in range(5):
        assert login(env, "alice", "bad").startswith(b"HTTP/1.1 401")
    r = login(env, "alice", "pw-a")
    assert r.startswith(b"HTTP/1.1 429")
    assert signed_in(env, "bob")                                     # other users are unaffected


def test_removing_a_user_from_the_file_ends_their_session(env):
    token = signed_in(env, "alice")
    env.users_file.write_text("bob\n")
    assert send(env, req("GET", "/", [f"Cookie: clh_session={token}"])).startswith(b"HTTP/1.1 401")


def test_logout_ends_the_session(env):
    token = signed_in(env, "alice")
    r = send(env, req("POST", "/__clh/logout", [f"Cookie: clh_session={token}"]))
    assert r.startswith(b"HTTP/1.1 303") and b"Max-Age=0" in r
    assert send(env, req("GET", "/", [f"Cookie: clh_session={token}"])).startswith(b"HTTP/1.1 401")


def test_expired_session_is_rejected(env):
    token = signed_in(env, "alice")
    env.gate.sessions[token]["born"] -= env.gate.session_secs + 1
    assert send(env, req("GET", "/", [f"Cookie: clh_session={token}"])).startswith(b"HTTP/1.1 401")


def test_pipelined_second_request_is_not_forwarded(env):
    token = signed_in(env, "alice")
    ok = req("GET", "/one", [f"Cookie: clh_session={token}"])
    sneaky = req("GET", "/admin")                                    # no cookie: must never reach the editor
    send(env, ok + sneaky)
    head, _, extra = env.editors[2001].received[-1]
    assert b"/one" in head and extra == b"" and b"/admin" not in head


def test_post_body_is_forwarded_exactly(env):
    token = signed_in(env, "alice")
    send(env, req("POST", "/save", [f"Cookie: clh_session={token}"], b"hello=world") + b"GET /x HTTP/1.1\r\n\r\n")
    _, body, extra = env.editors[2001].received[-1]
    assert body == b"hello=world" and extra == b""


def test_chunked_and_conflicting_lengths_are_refused(env):
    token = signed_in(env, "alice")
    c = f"Cookie: clh_session={token}"
    assert send(env, req("POST", "/x", [c, "Transfer-Encoding: chunked"])).startswith(b"HTTP/1.1 501")
    both = req("POST", "/x", [c, "Content-Length: 3", "Content-Length: 5"])
    assert send(env, both).startswith(b"HTTP/1.1 400")
    assert env.editors[2001].received == []


def test_websocket_upgrade_is_relayed_both_ways_once_signed_in(env):
    token = signed_in(env, "alice")
    ws = req("GET", "/stable", [f"Cookie: clh_session={token}", "Connection: Upgrade", "Upgrade: websocket"])
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5)
    c.connect(env.path)
    c.sendall(ws)
    assert c.recv(4096).startswith(b"HTTP/1.1 101")
    c.sendall(b"ping")
    assert c.recv(4096) == b"ws:ping"
    c.close()
    assert env.editors[2001].received


def test_editor_is_started_when_its_socket_is_down(env, tmp_path):
    token = signed_in(env, "alice")
    env.editors[2001].srv.close()
    os.unlink(env.gate.editor_socket(2001))

    def start(user):
        env.started.append(user)
        env.editors[2001] = FakeEditor(env.gate.editor_socket(2001), 2001)
    env.gate.start_editor = start
    assert send(env, req("GET", "/", [f"Cookie: clh_session={token}"])).endswith(b"editor:2001")
    assert "alice" in env.started


def test_oversized_head_is_rejected(env):
    assert send(env, b"GET / HTTP/1.1\r\n" + b"X: " + b"a" * 20000 + b"\r\n\r\n").startswith(b"HTTP/1.1 431")


def test_pam_check_rejects_a_bad_password_without_crashing():
    if not gatemod.ctypes.util.find_library("pam"):
        pytest.skip("libpam not installed")
    assert gatemod.pam_check("login", "no-such-user-xyz", "x") is False
