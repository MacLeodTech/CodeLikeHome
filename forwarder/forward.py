#!/usr/bin/env python3
"""codelikehome-forward: relay a TCP port to a Unix socket.

Lets a container on a private network (a bastion, say) reach a service that listens only on a mode-600 Unix socket on the
host, with no TCP port exposed anywhere else. It copies bytes in both directions and understands nothing about them, so
websockets and any other protocol pass straight through. The socket's directory is the only thing mounted into the container.

Environment:
  FORWARD_UNIX     path of the Unix socket to connect to (required)
  FORWARD_LISTEN   TCP port to listen on (default 8443)
  FORWARD_MAX      most simultaneous connections (default 64)
  FORWARD_ALLOW    comma-separated host names (e.g. "warpgate") whose addresses may connect; anyone else is dropped. Names are looked up
                   on every connection, so a container that is recreated with a new address keeps working. Empty = no check.
"""
import os
import select
import socket
import socketserver
import sys
import threading

UNIX = os.environ.get("FORWARD_UNIX", "")
PORT = int(os.environ.get("FORWARD_LISTEN", "8443"))
MAX = int(os.environ.get("FORWARD_MAX", "64"))
ALLOW = [name.strip() for name in os.environ.get("FORWARD_ALLOW", "").split(",") if name.strip()]
_slots = threading.BoundedSemaphore(MAX)


def allowed(peer):
    """True when no allow-list is set, or `peer` is an address one of the allowed names currently resolves to."""
    if not ALLOW:
        return True
    peer = peer.removeprefix("::ffff:")
    for name in ALLOW:
        try:
            if any(info[4][0] == peer for info in socket.getaddrinfo(name, None, socket.AF_INET)):
                return True
        except OSError:
            continue                                  # a name that does not resolve allows nobody
    return False


def relay(a, b, idle=900):
    """Copy bytes both ways between two sockets until both sides have finished (or nothing moves for `idle` seconds)."""
    peer, watch = {a: b, b: a}, {a, b}
    while watch:
        ready, _, _ = select.select(list(watch), [], [], idle)
        if not ready:
            return
        for src in ready:
            data = src.recv(65536)
            if data:
                peer[src].sendall(data)
            else:                                   # that side is done sending: pass the half-close on
                watch.discard(src)
                try:
                    peer[src].shutdown(socket.SHUT_WR)
                except OSError:
                    pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not allowed(self.client_address[0]):
            print(f"refused connection from {self.client_address[0]}: not one of {', '.join(ALLOW)}", file=sys.stderr, flush=True)
            return
        if not _slots.acquire(blocking=False):
            print(f"dropped connection from {self.client_address[0]}: over the limit of {MAX}", file=sys.stderr, flush=True)
            return
        print(f"accepted connection from {self.client_address[0]}", flush=True)       # audit trail: who reached the editor, and when (docker logs adds the time)
        up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            up.settimeout(10)
            up.connect(UNIX)
            up.settimeout(None)
            relay(self.request, up)
        except OSError as e:
            print(f"forward to {UNIX} failed: {e}", file=sys.stderr, flush=True)
        finally:
            up.close()
            _slots.release()


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    if not UNIX:
        sys.exit("FORWARD_UNIX is required")
    print(f"codelikehome-forward :{PORT} -> {UNIX}", flush=True)
    Server(("0.0.0.0", PORT), Handler).serve_forever()
