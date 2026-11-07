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
    category: str = "protocol"


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

    def add(self, level, code, probe, message, hint, *, category="protocol"):
        self.findings.append(Finding(level, code, probe, message, hint, category))

    def finish(self, *, incomplete=False, unsupported=False):
        errors = [finding for finding in self.findings if finding.level == "error"]
        if any(finding.category == "protocol" for finding in errors):
            self.verdict = "fail"
        elif incomplete or any(finding.category == "operational" for finding in errors):
            self.verdict = "inconclusive"
        elif unsupported:
            self.verdict = "unsupported"
        elif self.findings:
            self.verdict = "warn"
        else:
            self.verdict = "pass"
        return self

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
    if type(sample_bytes) is not int or not 2 <= sample_bytes <= 65536:
        raise ValueError("sample_bytes must be between 2 and 65536.")
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
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
                category="protocol" if response.error == "incomplete_body" else "operational",
            )
        elif (
            response.status is not None
            and response.status >= 400
            and not (
                response.status == 416
                and name in {"prefix", "past_end"}
                or response.status == 501
                and name == "suffix"
            )
        ):
            if response.status in {401, 403, 407}:
                hint = "Check authentication and signed-URL expiry; use the direct file URL."
            elif response.status == 429:
                hint = "Wait for the server's rate limit to reset before retrying."
            elif response.status in {404, 410}:
                hint = "Check that the file still exists and that this is the correct direct URL."
            else:
                hint = "Resolve the HTTP error at the server or proxy, then rerun the diagnostic."
            report.add(
                "error",
                "http_error",
                name,
                f"HTTP {response.status} prevented this probe from completing.",
                hint,
                category="operational",
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

    def interrupted(name, response):
        return response.error is not None or any(
            finding.probe == name and finding.category == "operational" and finding.level == "error"
            for finding in report.findings
        )

    def check_etag(name, response):
        if initial_etag is not None and response.headers.get("etag") != initial_etag:
            report.add(
                "error",
                "etag_changed",
                name,
                "ETag changed or disappeared between probes.",
                "Check inconsistent cache nodes or a file being replaced during download.",
            )

    def compare_bytes(name, start, body):
        for old_start, old_body in samples:
            lo = max(start, old_start)
            hi = min(start + len(body), old_start + len(old_body))
            if (
                lo < hi
                and body[lo - start : hi - start] != old_body[lo - old_start : hi - old_start]
            ):
                report.add(
                    "error",
                    "bytes_changed",
                    name,
                    "Overlapping byte ranges contain different bytes.",
                    "Do not combine these responses; check mutable files or stale range caches.",
                )
                break

    def check_full_fallback(response):
        name = "if_range_mismatch"
        if response.duplicate_headers:
            return
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            report.add(
                "error",
                "encoded_response",
                name,
                "Full fallback used content encoding despite requesting identity.",
                "Serve the same identity representation for full and partial responses.",
            )
            return
        declared = response.headers.get("content-length")
        if declared is not None and (
            not re.fullmatch(r"[0-9]{1,20}", declared) or int(declared) != report.size_bytes
        ):
            report.add(
                "error",
                "full_length_changed",
                name,
                "Full-response Content-Length differs from the previously observed total.",
                "Check for a replaced file or incorrect full-response length metadata.",
            )
        expected_read = min(report.size_bytes, sample_bytes + 1)
        if len(response.body) != expected_read:
            report.add(
                "error",
                "wrong_body_length",
                name,
                f"Expected a {expected_read}-byte sample; read {len(response.body)} bytes.",
                "Check empty, truncated or inconsistent full-response bodies.",
            )
        check_etag(name, response)
        compare_bytes(name, 0, response.body)

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
                    category="capability" if first else "protocol",
                )
            else:
                report.add(
                    "error",
                    "unexpected_status",
                    name,
                    f"Expected 206; received HTTP {response.status}.",
                    "Check access, URL expiry, rate limits and server range support.",
                    category="operational",
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
        if start < expected_start or end > expected_end:
            report.add(
                "error",
                "wrong_range",
                name,
                f"Requested {expected_start}-{expected_end}; received {start}-{end}.",
                "Check CDN cache keys and proxy forwarding of the Range header.",
            )
            return None
        if start > expected_start or end < expected_end:
            # RFC 9110 allows only a portion of a requested range to be transferred.
            report.add(
                "warning",
                "partial_range",
                name,
                "Server returned a contained subset of the requested range.",
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
            check_etag(name, response)
        compare_bytes(name, start, response.body)
        samples.append((start, response.body))
        return span

    prefix = request("prefix", f"bytes=0-{sample_bytes - 1}")
    if interrupted("prefix", prefix):
        return report.finish(incomplete=True)
    empty = UNSATISFIED.fullmatch(prefix.headers.get("content-range", ""))
    if prefix.status == 416 and empty and len(empty[1]) <= 20 and int(empty[1]) == 0:
        report.add(
            "warning",
            "empty_resource",
            "prefix",
            "The resource is empty.",
            "There are no bytes available to test range behavior.",
        )
        return report.finish(incomplete=True)
    span = check_range("prefix", prefix, 0, sample_bytes - 1, first=True)
    if span is None:
        return report.finish(
            unsupported=prefix.status == 200, incomplete=prefix.status not in {200, 206}
        )
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
        return report.finish(incomplete=True)
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
        if interrupted(name, response):
            return report.finish(incomplete=True)
        check_range(name, response, start, end)
    tail = request("suffix", f"bytes=-{sample_bytes}")
    if interrupted("suffix", tail):
        return report.finish(incomplete=True)
    tail_start = max(0, size - sample_bytes)
    if tail.status == 501:
        report.add(
            "warning",
            "suffix_unsupported",
            "suffix",
            "Suffix syntax returned HTTP 501; testing the explicit tail offsets instead.",
            "Use explicit START-END byte ranges on this endpoint.",
            category="capability",
        )
        tail = request("explicit_tail", f"bytes={tail_start}-{size - 1}")
        if interrupted("explicit_tail", tail):
            return report.finish(incomplete=True)
        check_range("explicit_tail", tail, tail_start, size - 1)
    else:
        check_range("suffix", tail, tail_start, size - 1)
    match = initial_etag if report.validator == "strong" else None
    repeat = request("if_range_match" if match else "repeat", f"bytes=0-{sample_bytes - 1}", match)
    if interrupted("if_range_match" if match else "repeat", repeat):
        return report.finish(incomplete=True)
    check_range("if_range_match" if match else "repeat", repeat, 0, min(sample_bytes, size) - 1)

    # A deliberately different, syntactically valid strong ETag must fall back to 200.
    mismatch = '"rangeglass-' + uuid.uuid4().hex + '"'
    different = request("if_range_mismatch", f"bytes=0-{sample_bytes - 1}", mismatch)
    if interrupted("if_range_mismatch", different):
        return report.finish(incomplete=True)
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
                "error",
                "if_range_unverified",
                "if_range_mismatch",
                f"Expected 200 for a mismatching validator; received HTTP {different.status}.",
                "Conditional resume could not be confirmed; inspect origin/proxy policy.",
                category="operational",
            )
            return report.finish(incomplete=True)
        else:
            check_full_fallback(different)

    outside = request("past_end", f"bytes={size}-")
    if interrupted("past_end", outside):
        return report.finish(incomplete=True)
    if not outside.error:
        expected = f"bytes */{size}"
        boundary = UNSATISFIED.fullmatch(outside.headers.get("content-range", ""))
        if outside.status == 206:
            report.add(
                "error",
                "past_end_partial",
                "past_end",
                "Server returned partial content for an offset beyond the observed file.",
                "Check stale range caches, a changed file size, or incorrect range handling.",
            )
        elif (
            outside.status == 416
            and boundary
            and len(boundary[1]) <= 20
            and int(boundary[1]) != size
        ):
            report.add(
                "error",
                "size_changed",
                "past_end",
                "The end-of-file response reports a different total size.",
                "Use a stable versioned URL; do not combine responses from different versions.",
            )
        elif outside.status != 416 or outside.headers.get("content-range", "").lower() != expected:
            report.add(
                "warning",
                "end_boundary_unverified",
                "past_end",
                "An out-of-bounds range did not return 416 with the expected total size.",
                "Inspect end-of-file handling; a 200 fallback must never be appended.",
            )
    return report.finish()
