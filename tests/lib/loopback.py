"""A real loopback HTTP server on ``127.0.0.1`` for exercising the adapters' REAL
wire client (:func:`conjured.adapters.wire.urllib_transport`) end to end — no
external network, and no fake occupying the transport seam (the point: the
``urllib`` POST, including its ``HTTPError`` → ``(status, body)`` conversion,
actually runs).

The responder is any callable with the transport protocol
``(url, body_bytes, headers, timeout_s) -> (status, body_bytes)`` — the recording
fakes in :mod:`tests.lib.fakes` satisfy it, so one scripted server logic drives both
the seam fakes and the real-wire tests (the handler passes the request *path* as the
``url`` argument, which the fakes' route checks accept)."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server's fixed dispatch name
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        status, payload = self.server.responder(
            self.path, body, dict(self.headers), None
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # quiet test output
        pass


@contextmanager
def loopback_server(responder):
    """Serve ``responder`` on an ephemeral ``127.0.0.1`` port for the block's
    duration; yields the base URL (``http://127.0.0.1:<port>``)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.responder = responder
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
