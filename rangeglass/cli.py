"""Command-line entry point and compact, pasteable reports."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata

from . import __version__
from .core import diagnose

TITLES = {
    "pass": "PASS - sampled ranges and conditional resume behaved correctly",
    "warn": "WARN - ranges work, with caveats",
    "fail": "FAIL - inconsistent or unsafe-to-combine responses observed",
    "unsupported": "UNSUPPORTED - server ignored the range request",
    "inconclusive": "INCONCLUSIVE - not enough evidence to complete the checks",
}


def visible(value):
    return "".join(
        c if unicodedata.category(c)[0] != "C" else f"\\u{ord(c):04x}" for c in str(value)
    )


def format_text(report):
    lines = [f"Rangeglass {__version__}", TITLES[report.verdict], "", visible(report.url)]
    size = f"{report.size_bytes:,} bytes" if report.size_bytes is not None else "unknown"
    lines.append(f"Resource: {size} | ETag: {report.validator}")
    lines.append(f"Read: {report.bytes_read:,} body bytes across {len(report.probes)} probes")
    lines.append("")
    for probe in report.probes:
        state = str(probe["status"]) if probe["status"] is not None else "ERR"
        details = probe["headers"].get("content-range", "-")
        if probe["error"]:
            details = probe["error"]
        lines.append(f"  {probe['name']:<19} {state:>3}  {visible(details)}")
    if report.findings:
        lines.append("")
        for finding in report.findings:
            lines.append(f"[{finding.level.upper()}] {finding.code} ({finding.probe})")
            lines.append("  " + visible(finding.message))
            lines.append("  Next: " + visible(finding.hint))
    lines.extend(
        ["", "A sample check, not a checksum. Verify the completed file with a trusted hash."]
    )
    return "\n".join(lines) + "\n"


def format_markdown(report):
    text = format_text(report)
    # Keep untrusted headers and URL paths inside one literal code block,
    # even if a response includes backticks or HTML.
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"### Rangeglass diagnostic\n\n{fence}text\n{text}{fence}\n"


def parser():
    p = argparse.ArgumentParser(
        description="Check HTTP byte ranges and conditional resume before a large download.",
        epilog="Exit codes: 0 passed, 1 findings/unsupported, 2 incomplete/error.",
    )
    p.add_argument("url", nargs="?", help="direct HTTP(S) file URL; quote URLs containing &")
    p.add_argument("--version", action="version", version=f"rangeglass {__version__}")
    output = p.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="emit a machine-readable report")
    output.add_argument(
        "--markdown", action="store_true", help="emit a report to paste into an issue"
    )
    p.add_argument("--strict", action="store_true", help="also exit 1 for warnings")
    p.add_argument(
        "--sample-bytes",
        type=int,
        default=1024,
        metavar="N",
        help="bytes per sample, 2..65536 (default: 1024)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=10,
        metavar="SECONDS",
        help="socket timeout and body-loop time budget (default: 10)",
    )
    p.add_argument(
        "-H",
        "--header",
        action="append",
        default=[],
        metavar="NAME:VALUE",
        help="custom request header; values are omitted from reports",
    )
    p.add_argument(
        "--header-env",
        action="append",
        default=[],
        metavar="NAME=VARIABLE",
        help="read a complete header value from an environment variable",
    )
    p.add_argument("--no-proxy", action="store_true", help="ignore system/environment HTTP proxies")
    p.add_argument(
        "--demo",
        choices=["good", "broken", "ignores-range"],
        help="check a temporary local fixture; no internet connection needed",
    )
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if bool(args.url) == bool(args.demo):
        p.error("provide either a URL or --demo")
    headers = {}
    try:
        for specification in args.header:
            name, sep, value = specification.partition(":")
            if not sep or name.lower().strip() in headers:
                raise ValueError("Use NAME:VALUE once for each custom header.")
            headers[name.lower().strip()] = value.strip()
        for specification in args.header_env:
            name, sep, variable = specification.partition("=")
            if not sep or not variable or name.lower().strip() in headers:
                raise ValueError("Use NAME=VARIABLE once for each custom header.")
            if variable not in os.environ:
                raise ValueError("A --header-env environment variable is not set.")
            headers[name.lower().strip()] = os.environ[variable]
        if args.demo and headers:
            raise ValueError("Custom headers are not needed for the local demo.")
        options = dict(
            sample_bytes=args.sample_bytes,
            timeout=args.timeout,
            headers=headers,
            use_proxy=not args.no_proxy,
        )
        if args.demo:
            from .demo import demo_server

            with demo_server() as base:
                options["use_proxy"] = False
                report = diagnose(f"{base}/{args.demo}", **options)
        else:
            report = diagnose(args.url, **options)
    except ValueError as exc:
        p.error(str(exc))
    except KeyboardInterrupt:
        print("rangeglass: interrupted", file=sys.stderr)
        return 130
    try:
        if args.json:
            output = json.dumps(report.to_dict(args.strict), indent=2, ensure_ascii=True) + "\n"
        elif args.markdown:
            output = format_markdown(report)
        else:
            output = format_text(report)
        sys.stdout.write(output)
        sys.stdout.flush()
    except (OSError, UnicodeError):
        # Do not report a successful check if the result could not be delivered.
        return 2
    return report.exit_code(args.strict)
