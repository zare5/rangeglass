"""Small loopback-only fixtures for the documented offline demonstration."""

from __future__ import annotations

import re
import socketserver
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = bytes(range(256)) * 32
ETAG = '"rangeglass-demo-v1"'


class LoopbackServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer normally resolves its own hostname. An offline fixture
        # needs neither reverse DNS nor a working network resolver.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


class DemoHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        mode = self.path.strip("/")
        requested = self.headers.get("Range", "")
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
        mismatch = self.headers.get("If-Range") not in {None, ETAG}
        if mode == "ignores-range" or not match or mismatch:
            self.reply(200, DATA)
            return
        if match[1]:
            start = int(match[1])
            end = min(int(match[2]) if match[2] else len(DATA) - 1, len(DATA) - 1)
        else:
            start, end = max(0, len(DATA) - int(match[2])), len(DATA) - 1
        if start >= len(DATA):
            self.reply(416, b"", f"bytes */{len(DATA)}")
            return
        if mode == "broken" and start > 0:
            # Reproduce a cache serving the first chunk for every requested offset.
            start, end = 0, end - start
        self.reply(206, DATA[start : end + 1], f"bytes {start}-{end}/{len(DATA)}")

    def reply(self, status, body, content_range=None):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", ETAG)
        if content_range:
            self.send_header("Content-Range", content_range)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


@contextmanager
def demo_server():
    server = LoopbackServer(("127.0.0.1", 0), DemoHandler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
