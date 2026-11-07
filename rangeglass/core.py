"""Evidence-based range probes; a passing sample is not a file integrity guarantee."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field

from . import __version__
from .transport import REPORTED_HEADERS, Response, Transport, redact_url, validate_url

CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+|\*)\Z", re.IGNORECASE)
UNSATISFIED = re.compile(r"bytes \*/([0-9]+)\Z", re.IGNORECASE)
STRONG_ETAG = re.compile(r'"[\x21\x23-\x7e\x80-\xff]*"\Z')


@dataclass
class Finding:
    level: str
    code: str
    probe: str
    message: str
    hint: str


@dataclass
class Report:
    url: str
    sample_bytes: int
    schema_version: int = 1
    version: str = __version__
    verdict: str = "inconclusive"
    size_bytes: int | None = None
    validator: str = "missing"
    bytes_read: int = 0
    probes: list[dict] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def add(self, level, code, probe, message, hint):
        self.findings.append(Finding(level, code, probe, message, hint))

    def exit_code(self, strict=False):
        if self.verdict == "inconclusive":
            return 2
        if self.verdict in {"fail", "unsupported"} or (strict and self.verdict == "warn"):
            return 1
        return 0

    def to_dict(self, strict=False):
        return {**asdict(self), "exit_code": self.exit_code(strict)}


def parse_content_range(value: str):
    match = CONTENT_RANGE.fullmatch(value)
    if not match:
        return None
    # Avoid unbounded integer conversion of hostile header values.
    if any(len(v) > 20 for v in match.groups()):
        return None
    start, end = int(match[1]), int(match[2])
    total = None if match[3] == "*" else int(match[3])
    if end < start or (total is not None and end >= total):
        return None
    return start, end, total


def etag_kind(value: str | None) -> str:
    if not value:
        return "missing"
    if STRONG_ETAG.fullmatch(value):
        return "strong"
    if value.startswith("W/") and STRONG_ETAG.fullmatch(value[2:]):
        return "weak"
    return "invalid"


def diagnose(url: str, *, sample_bytes=1024, timeout=10, headers=None, use_proxy=True) -> Report:
    url = validate_url(url)
    if not 2 <= sample_bytes <= 65536:
        raise ValueError("sample_bytes must be between 2 and 65536.")
    if not 0 < timeout <= 120:
        raise ValueError("timeout must be greater than 0 and at most 120 seconds.")
    transport = Transport(timeout=timeout, headers=headers, use_proxy=use_proxy)
    report = Report(redact_url(url), sample_bytes)
    samples: list[tuple[int, bytes]] = []
    initial_etag = None

    def request(name, value, validator=None):
        response = transport.get(url, value, sample_bytes + 1, validator)
        report.bytes_read += len(response.body)
        report.probes.append(
            {
                "name": name,
                "range": value,
                "if_range": ("matching" if validator == initial_etag else "mismatching")
                if validator
                else None,
                "status": response.status,
                "headers": {
                    k: response.headers[k] for k in REPORTED_HEADERS if k in response.headers
                },
                "etag_kind": etag_kind(response.headers.get("etag")),
                "bytes_read": len(response.body),
                "url": response.url,
                "redirects": response.redirects,
                "error": response.error,
            }
        )
        if response.error:
            report.add(
                "error",
                response.error,
                name,
                "Could not complete this probe.",
                "Check the URL, TLS certificate, proxy and timeout; retry the diagnostic.",
            )
        if response.duplicate_headers:
            report.add(
                "error",
                "duplicate_headers",
                name,
                "Ambiguous response headers: " + ", ".join(response.duplicate_headers),
                "Return one unambiguous value for each representation/framing header.",
            )
        if "content-length" in response.headers and "transfer-encoding" in response.headers:
            report.add(
                "error",
                "ambiguous_framing",
                name,
                "Response contains both Content-Length and Transfer-Encoding.",
                "Fix HTTP response framing at the origin or proxy.",
            )
        return response

    def check_range(name: str, response: Response, expected_start, expected_end, *, first=False):
        if response.error or response.duplicate_headers:
            return None
        if response.status != 206:
            if response.status == 200:
                report.add(
                    "error",
                    "range_ignored",
                    name,
                    "Server returned 200 (full response) to a byte-range request.",
                    "Use a full download or fix Range forwarding; do not append this body.",
                )
            else:
                report.add(
                    "error",
                    "unexpected_status",
                    name,
                    f"Expected 206; received HTTP {response.status}.",
                    "Check access, URL expiry, rate limits and server range support.",
                )
            return None
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            report.add(
                "error",
                "encoded_response",
                name,
                "Received encoded content despite requesting identity encoding.",
                "Disable transport compression for this endpoint before combining byte ranges.",
            )
            return None
        span = parse_content_range(response.headers.get("content-range", ""))
        if span is None:
            report.add(
                "error",
                "invalid_content_range",
                name,
                "206 response has a missing or invalid Content-Range.",
                "Return Content-Range: bytes START-END/TOTAL for each requested range.",
            )
            return None
        start, end, total = span
        expected_end = min(expected_end, total - 1) if first and total is not None else expected_end
        if start != expected_start or end > expected_end:
            report.add(
                "error",
                "wrong_range",
                name,
                f"Requested {expected_start}-{expected_end}; received {start}-{end}.",
                "Check CDN cache keys and proxy forwarding of the Range header.",
            )
            return None
        if end < expected_end:
            # RFC 9110 allows only a portion of a requested range to be transferred.
            report.add(
                "warning",
                "partial_range",
                name,
                "Server returned a shorter portion of the requested range.",
                "The downloader must request the remaining bytes separately.",
            )
        length = end - start + 1
        declared = response.headers.get("content-length")
        if declared is not None and (
            not re.fullmatch(r"[0-9]{1,20}", declared) or int(declared) != length
        ):
            report.add(
                "error",
                "wrong_content_length",
                name,
                "Content-Length disagrees with the returned byte range.",
                "Set Content-Length to END - START + 1, or use correct chunked framing.",
            )
            return None
        if len(response.body) != length:
            report.add(
                "error",
                "wrong_body_length",
                name,
                f"Range requires {length} bytes; read {len(response.body)}.",
                "Check truncation, buffering and compression between client and origin.",
            )
            return None
        if not first:
            if total != report.size_bytes:
                report.add(
                    "error",
                    "size_changed",
                    name,
                    "Total resource size changed or disappeared between probes.",
                    "Use a versioned, immutable download URL and rerun the check.",
                )
            current_etag = response.headers.get("etag")
            if initial_etag is not None and current_etag != initial_etag:
                report.add(
                    "error",
                    "etag_changed",
                    name,
                    "ETag changed or disappeared between probes.",
                    "Check inconsistent cache nodes or a file being replaced during download.",
                )
        for old_start, old_body in samples:
            lo = max(start, old_start)
            hi = min(end + 1, old_start + len(old_body))
            if (
                lo < hi
                and response.body[lo - start : hi - start]
                != old_body[lo - old_start : hi - old_start]
            ):
                report.add(
                    "error",
                    "bytes_changed",
                    name,
                    "Overlapping byte ranges contain different bytes.",
                    "Do not combine these responses; check mutable files or stale range caches.",
                )
                break
        samples.append((start, response.body))
        return span

    prefix = request("prefix", f"bytes=0-{sample_bytes - 1}")
    if prefix.error:
        return report
    empty = UNSATISFIED.fullmatch(prefix.headers.get("content-range", ""))
    if prefix.status == 416 and empty and len(empty[1]) <= 20 and int(empty[1]) == 0:
        report.add(
            "warning",
            "empty_resource",
            "prefix",
            "The resource is empty.",
            "There are no bytes available to test range behavior.",
        )
        return report
    span = check_range("prefix", prefix, 0, sample_bytes - 1, first=True)
    if span is None:
        report.verdict = (
            "unsupported"
            if prefix.status == 200
            else ("fail" if prefix.status == 206 else "inconclusive")
        )
        return report
    report.size_bytes = span[2]
    initial_etag = prefix.headers.get("etag")
    report.validator = etag_kind(initial_etag)
    if report.size_bytes is None:
        report.add(
            "warning",
            "unknown_size",
            "prefix",
            "Server returned a valid range without a total size.",
            "Tail, middle and end-of-file checks need a known total; these checks were skipped.",
        )
        return report
    size = report.size_bytes
    if report.validator != "strong":
        report.add(
            "warning",
            "no_strong_etag",
            "prefix",
            "No usable strong ETag; matching conditional resume was not tested.",
            "Serve a stable strong ETag, or use an immutable URL and verify the final checksum.",
        )
    specs = [
        ("overlap", min(sample_bytes // 2, size - 1)),
        ("middle", size // 2),
    ]
    for name, start in specs:
        end = min(start + sample_bytes - 1, size - 1)
        response = request(name, f"bytes={start}-{end}")
        check_range(name, response, start, end)
    tail = request("suffix", f"bytes=-{sample_bytes}")
    check_range("suffix", tail, max(0, size - sample_bytes), size - 1)
    match = initial_etag if report.validator == "strong" else None
    repeat = request("if_range_match" if match else "repeat", f"bytes=0-{sample_bytes - 1}", match)
    check_range("if_range_match" if match else "repeat", repeat, 0, min(sample_bytes, size) - 1)

    # A deliberately different, syntactically valid strong ETag must fall back to 200.
    mismatch = '"rangeglass-' + uuid.uuid4().hex + '"'
    different = request("if_range_mismatch", f"bytes=0-{sample_bytes - 1}", mismatch)
    if not different.error:
        if different.status == 206:
            report.add(
                "error",
                "if_range_ignored",
                "if_range_mismatch",
                "Server returned partial content for a mismatching If-Range ETag.",
                "Honor If-Range so clients cannot splice bytes from different file versions.",
            )
        elif different.status != 200:
            report.add(
                "warning",
                "if_range_unverified",
                "if_range_mismatch",
                f"Expected 200 for a mismatching validator; received HTTP {different.status}.",
                "Conditional resume could not be confirmed; inspect origin/proxy policy.",
            )
        elif different.headers.get("content-encoding", "identity").lower() == "identity":
            common = min(len(prefix.body), len(different.body), span[1] + 1)
            if prefix.body[:common] != different.body[:common]:
                report.add(
                    "error",
                    "bytes_changed",
                    "if_range_mismatch",
                    "The full-response prefix differs from the initial range.",
                    "Check inconsistent representations or a file that changed during the scan.",
                )

    outside = request("past_end", f"bytes={size}-")
    if not outside.error:
        expected = f"bytes */{size}"
        if outside.status != 416 or outside.headers.get("content-range", "").lower() != expected:
            report.add(
                "warning",
                "end_boundary_unverified",
                "past_end",
                "An out-of-bounds range did not return 416 with the expected total size.",
                "Inspect end-of-file handling; a 200 fallback must never be appended.",
            )
    if any(p["error"] for p in report.probes):
        report.verdict = "inconclusive"
    elif any(f.level == "error" for f in report.findings):
        report.verdict = "fail"
    elif report.findings:
        report.verdict = "warn"
    else:
        report.verdict = "pass"
    return report
