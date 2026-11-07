"""Independent HTTP fixtures; never contact third-party services in tests."""

import contextlib
import hashlib
import re
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(1024))


class FixtureServer(ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


@contextlib.contextmanager
def fixture(mode="good", data=DATA):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            calls.append(dict(self.headers))
            if mode == "redirect_chain":
                self.send_response(302)
                self.send_header("Location", f"/file?hop={len(calls)}")
                self.end_headers()
                return
            if mode == "redirect":
                if self.path.startswith("/file"):
                    self.send_response(302)
                    self.send_header("Location", "/target?signature=do-not-print")
                    self.end_headers()
                    return
            if mode in {"loop", "foreign", "bad_redirect", "no_location"}:
                self.send_response(302)
                if mode != "no_location":
                    self.send_header(
                        "Location",
                        {
                            "loop": "/file",
                            "foreign": server.target,
                            "bad_redirect": "file:///etc/passwd",
                        }.get(mode, "/file"),
                    )
                self.end_headers()
                return
            if mode == "forbidden":
                self.send_response(403)
                self.end_headers()
                return
            if mode == "timeout":
                time.sleep(0.15)
                self.send_response(200)
                self.end_headers()
                return
            if len(calls) > 1 and mode in {"late_403", "late_429"}:
                self.send_response(int(mode[-3:]))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if mode == "wrong_then_503" and len(calls) == 3:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if mode in {"suffix_501", "suffix_501_bad_tail"} and self.headers.get(
                "Range", ""
            ).startswith("bytes=-"):
                self.send_response(501)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            resource = data
            etag = '"fixture-v1"'
            if mode == "changing_etag" and len(calls) > 1:
                etag = '"fixture-v2"'
            if mode == "weak":
                etag = 'W/"fixture-v1"'
            if mode == "changing_size" and len(calls) > 1:
                resource += b"!"
            total = len(resource)
            range_value = self.headers.get("Range", "")
            condition = self.headers.get("If-Range")
            has_mismatch = condition is not None and condition != etag
            status, content_range, body = 200, None, resource
            if mode != "ignored" and (not has_mismatch or mode == "ignores_if_range"):
                found = re.fullmatch(r"bytes=(\d*)-(\d*)", range_value)
                if found:
                    if found[1]:
                        start = int(found[1])
                        end = min(int(found[2]) if found[2] else total - 1, total - 1)
                    else:
                        start, end = max(0, total - int(found[2])), total - 1
                    if start >= total:
                        status, body, content_range = 416, b"", f"bytes */{total}"
                        if mode == "ignores_end":
                            status, body, content_range = 200, resource, None
                        if mode == "past_end_206":
                            status, body, content_range = 206, resource[:1], f"bytes 0-0/{total}"
                        if mode == "past_end_size":
                            content_range = f"bytes */{total + 1}"
                    else:
                        status = 206
                        if mode in {"wrong_offset", "wrong_then_503"} and start > 0:
                            start, end = 0, end - start
                        if mode == "suffix_501_bad_tail" and end == total - 1 and start > 0:
                            start, end = 0, end - start
                        if mode == "short_range":
                            end = min(end, start + 15)
                        if mode == "partial_subset" and end > start:
                            start += 1
                            end = min(end, start + 15)
                        body = resource[start : end + 1]
                        content_range = f"bytes {start}-{end}/{total}"
                        if mode == "unknown_size":
                            content_range = f"bytes {start}-{end}/*"
                        if mode == "invalid_range":
                            content_range = "bytes nonsense"
                        if mode == "missing_range":
                            content_range = None
                        if mode == "changed_bytes" and len(calls) > 1:
                            body = bytes(b ^ 255 for b in body)
            if has_mismatch:
                if mode == "fallback_empty":
                    body = b""
                if mode == "fallback_short":
                    body = resource[:8]
                if mode == "fallback_etag":
                    etag = '"new-version"'
                if mode == "fallback_503":
                    status, body = 503, b""
                if mode == "fallback_204":
                    status, body = 204, b""
            self.send_response(status)
            if mode != "no_advertisement":
                self.send_header("Accept-Ranges", "bytes")
            if mode != "no_etag":
                self.send_header("ETag", etag)
            if content_range:
                self.send_header("Content-Range", content_range)
            if mode == "encoded" or (mode == "fallback_encoded" and has_mismatch):
                self.send_header("Content-Encoding", "gzip")
            use_chunks = mode in {"chunked", "long_chunked"}
            declared = len(body) + (1 if mode == "wrong_length" else 0)
            if mode == "fallback_length" and has_mismatch:
                declared += 100
            if use_chunks:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(declared))
            if mode == "duplicate_length":
                self.send_header("Content-Length", str(declared))
            if mode == "ambiguous_framing":
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if mode == "truncated" and status == 206:
                body = body[:-1]
            if mode == "long_chunked" and status == 206:
                body += b"x"
            try:
                if use_chunks:
                    if body:
                        self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(body)
            except (ConnectionResetError, BrokenPipeError):
                pass

    server = FixtureServer(("127.0.0.1", 0), Handler)
    server.target = "/file"
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/file", calls, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
