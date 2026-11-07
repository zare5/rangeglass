"""HTTP/1.1 transport with bounded body reads and explicit redirects."""

from __future__ import annotations

import http.client
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import __version__

REDIRECTS = {301, 302, 303, 307, 308}
TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
RESERVED_HEADERS = {
    "range",
    "if-range",
    "accept-encoding",
    "host",
    "connection",
    "content-length",
    "transfer-encoding",
    "proxy-authorization",
    "if-match",
    "if-none-match",
    "if-modified-since",
    "if-unmodified-since",
    "expect",
    "upgrade",
    "trailer",
    "te",
}
REPORTED_HEADERS = (
    "accept-ranges",
    "content-range",
    "content-length",
    "content-encoding",
    "content-type",
)


def validate_url(url: str) -> str:
    if not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError(
            "URL must not contain whitespace or control characters; percent-encode them."
        )
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Use an absolute http:// or https:// download URL.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL credentials are not accepted; use --header-env for authentication.")
    try:
        _ = parts.port  # Accessing this property validates the port.
        url.encode("ascii")
    except (ValueError, UnicodeError) as exc:
        raise ValueError(
            "Use a valid port, an ASCII/IDNA host, and percent-encoded URL paths."
        ) from exc
    return urllib.parse.urlunsplit(parts._replace(fragment=""))


def origin(url: str) -> tuple[str, str, int]:
    p = urllib.parse.urlsplit(url)
    port = p.port if p.port is not None else (443 if p.scheme == "https" else 80)
    return p.scheme, p.hostname.lower(), port


def redact_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(p._replace(query="REDACTED" if p.query else "", fragment=""))


def validate_headers(headers: dict[str, str]) -> dict[str, str]:
    result = {}
    for name, value in headers.items():
        if not TOKEN.fullmatch(name) or name.lower() in RESERVED_HEADERS:
            raise ValueError("Invalid or reserved custom header name.")
        if name.lower() in result:
            raise ValueError("Duplicate custom header name.")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Custom header values must not contain control characters.")
        try:
            value.encode("latin-1")
        except UnicodeError as exc:
            raise ValueError("Custom header values must be Latin-1 encodable.") from exc
        result[name.lower()] = value
    return result


class _RawResponses(urllib.request.HTTPErrorProcessor):
    # Bypass automatic redirects and HTTPError handling: both can consume bodies.
    def http_response(self, request, response):
        return response

    https_response = http_response


@dataclass
class Response:
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    redirects: list[dict] = field(default_factory=list)
    url: str = ""
    error: str | None = None
    duplicate_headers: list[str] = field(default_factory=list)


class Transport:
    def __init__(self, *, timeout: float = 10, headers=None, use_proxy: bool = True):
        self.timeout = timeout
        self.headers = validate_headers(headers or {})
        handlers = [_RawResponses()]
        if not use_proxy:
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)

    def get(self, url: str, range_value: str, cap: int, if_range: str | None = None) -> Response:
        url = validate_url(url)
        current = url
        custom = self.headers.copy()
        result = Response(url=redact_url(url))
        visited = set()
        for _ in range(6):  # At most five redirects per probe.
            if current in visited:
                result.error = "redirect_loop"
                return result
            visited.add(current)
            headers = {
                "user-agent": f"rangeglass/{__version__}",
                **custom,
                "accept-encoding": "identity",
                "range": range_value,
            }
            if if_range is not None:
                headers["if-range"] = if_range
            request = urllib.request.Request(current, headers=headers, method="GET")
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    result.url = redact_url(current)
                    result.status = response.status
                    if response.status in REDIRECTS:
                        if len(result.redirects) >= 5:
                            result.error = "too_many_redirects"
                            return result
                        locations = response.headers.get_all("location", [])
                        if len(locations) != 1:
                            result.error = "missing_or_duplicate_redirect_location"
                            return result
                        try:
                            target = validate_url(urllib.parse.urljoin(current, locations[0]))
                        except ValueError:
                            result.error = "invalid_redirect_url"
                            return result
                        if origin(current)[0] == "https" and origin(target)[0] == "http":
                            result.error = "https_downgrade_blocked"
                            return result
                        stripped = bool(custom) and origin(current) != origin(target)
                        if origin(current) != origin(target):
                            custom = {}  # Never restore credentials after crossing an origin.
                        result.redirects.append(
                            {
                                "status": response.status,
                                "from": redact_url(current),
                                "to": redact_url(target),
                                "custom_headers_stripped": stripped,
                            }
                        )
                        current = target
                        continue  # Do not read redirect bodies.
                    result.headers = {k.lower(): v.strip() for k, v in response.headers.items()}
                    result.duplicate_headers = [
                        key
                        for key in (
                            "content-range",
                            "content-length",
                            "content-encoding",
                            "content-type",
                            "etag",
                            "transfer-encoding",
                        )
                        if len(response.headers.get_all(key, [])) > 1
                    ]
                    chunks = []
                    remaining = cap
                    started = time.monotonic()
                    try:
                        while remaining:
                            if time.monotonic() - started > self.timeout:
                                result.error = "body_timeout"
                                break
                            chunk = response.read1(remaining)
                            if not chunk:
                                break
                            chunks.append(chunk)
                            remaining -= len(chunk)
                    except http.client.IncompleteRead as exc:
                        chunks.append(exc.partial[:remaining])
                        result.error = "incomplete_body"
                    except (OSError, http.client.HTTPException) as exc:
                        result.error = (
                            "body_timeout" if isinstance(exc, TimeoutError) else "body_read_error"
                        )
                    result.body = b"".join(chunks)
                    return result
            except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    result.error = "connection_timeout"
                elif isinstance(reason, ssl.SSLError):
                    result.error = "tls_error"
                else:
                    result.error = "connection_error"
                return result
        result.error = "too_many_redirects"
        return result
