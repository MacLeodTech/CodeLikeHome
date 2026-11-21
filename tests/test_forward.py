"""The TCP-to-Unix-socket forwarder: bytes go both ways, half-closes pass through, a missing socket just drops the connection."""
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "forwarder"))
import forward


def serve_unix(path, reply):
    """A tiny Unix-socket server that reads until EOF then answers with `reply(received)`."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        data = b""
        while chunk := conn.recv(4096):
            data += chunk
        conn.sendall(reply(data))
        conn.close()
        srv.close()
    threading.Thread(target=run, daemon=True).start()


def start_forwarder(monkeypatch, unix_path):
    monkeypatch.setattr(forward, "UNIX", unix_path)
    server = forward.Server(("127.0.0.1", 0), forward.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_bytes_pass_both_ways_and_half_close_works(tmp_path, monkeypatch):
    sock = str(tmp_path / "svc.sock")
    serve_unix(sock, lambda received: b"echo:" + received.upper())
    server = start_forwarder(monkeypatch, sock)
    with socket.create_connection(server.server_address, timeout=5) as c:
        c.sendall(b"hello")
        c.shutdown(socket.SHUT_WR)                         # the service answers only after it sees EOF
        got = b""
        while chunk := c.recv(4096):
            got += chunk
    assert got == b"echo:HELLO"
    server.shutdown()


def test_missing_socket_closes_the_connection(tmp_path, monkeypatch):
    server = start_forwarder(monkeypatch, str(tmp_path / "absent.sock"))
    with socket.create_connection(server.server_address, timeout=5) as c:
        assert c.recv(10) == b""
    server.shutdown()
    assert not os.path.exists(tmp_path / "absent.sock")


def test_only_allowed_peers_get_through(tmp_path, monkeypatch):
    sock = str(tmp_path / "svc.sock")
    serve_unix(sock, lambda received: b"ok")
    server = start_forwarder(monkeypatch, sock)
    monkeypatch.setattr(forward, "ALLOW", ["not-a-real-host.invalid"])             # does not resolve: allows nobody
    with socket.create_connection(server.server_address, timeout=5) as c:
        assert c.recv(10) == b""
    monkeypatch.setattr(forward, "ALLOW", ["localhost"])                           # resolves to the connecting address
    with socket.create_connection(server.server_address, timeout=5) as c:
        c.shutdown(socket.SHUT_WR)
        assert c.recv(10) == b"ok"
    server.shutdown()


def test_no_allow_list_means_no_check():
    assert forward.allowed("203.0.113.9") is True


def test_accepted_connections_are_logged(tmp_path, monkeypatch, capsys):
    sock = str(tmp_path / "svc.sock")
    serve_unix(sock, lambda received: b"ok")
    server = start_forwarder(monkeypatch, sock)
    with socket.create_connection(server.server_address, timeout=5) as c:
        c.shutdown(socket.SHUT_WR)
        assert c.recv(10) == b"ok"
    server.shutdown()
    assert "accepted connection from 127.0.0.1" in capsys.readouterr().out
