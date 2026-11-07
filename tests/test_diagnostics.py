import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from rangeglass.cli import main
from rangeglass.core import diagnose, parse_content_range
from rangeglass.demo import demo_server
from rangeglass.transport import Transport, redact_url, validate_headers, validate_url

from .servers import DATA, fixture


class DiagnosticTests(unittest.TestCase):
    def scan(self, mode="good", **kwargs):
        with fixture(mode, **kwargs) as (url, calls, _):
            report = diagnose(url, sample_bytes=64, timeout=1, use_proxy=False)
        self.assertLessEqual(report.bytes_read, 7 * 65)
        self.assertLessEqual(len(calls), 7)
        return report

    def test_good(self):
        report = self.scan()
        self.assertEqual(report.verdict, "pass")
        self.assertEqual(report.size_bytes, len(DATA))
        self.assertEqual(len(report.probes), 7)
        self.assertEqual(report.findings, [])
        self.assertEqual(report.exit_code(), 0)

    def test_content_range_parser(self):
        for bad in [
            "",
            "bytes 2-1/3",
            "bytes 0-3/3",
            "bytes */0",
            "bytes 0-1/0",
            "bytes -1-2/3",
            "bytes 0-1/" + "9" * 100,
            "items 0-1/3",
        ]:
            with self.subTest(value=bad):
                self.assertIsNone(parse_content_range(bad))
        self.assertEqual(parse_content_range("bytes 0-1/*"), (0, 1, None))
        self.assertEqual(parse_content_range("BYTES 1-2/3"), (1, 2, 3))

    def test_advertisement_not_required(self):
        self.assertEqual(self.scan("no_advertisement").verdict, "pass")

    def test_advertisement_is_not_proof(self):
        report = self.scan("ignored")
        self.assertEqual(report.verdict, "unsupported")
        self.assertEqual(report.exit_code(), 1)
        self.assertEqual(len(report.probes), 1)

    def test_failure_modes(self):
        cases = {
            "wrong_offset": "wrong_range",
            "wrong_length": "wrong_content_length",
            "truncated": "wrong_body_length",
            "missing_range": "invalid_content_range",
            "invalid_range": "invalid_content_range",
            "encoded": "encoded_response",
            "changing_etag": "etag_changed",
            "changing_size": "size_changed",
            "changed_bytes": "bytes_changed",
            "ignores_if_range": "if_range_ignored",
            "duplicate_length": "duplicate_headers",
            "long_chunked": "wrong_body_length",
        }
        for mode, code in cases.items():
            with self.subTest(mode=mode):
                report = self.scan(mode)
                self.assertEqual(report.verdict, "fail")
                self.assertIn(code, {f.code for f in report.findings})
                self.assertEqual(report.exit_code(), 1)

    def test_chunked_range(self):
        self.assertEqual(self.scan("chunked").verdict, "pass")

    def test_missing_and_weak_validators(self):
        for mode in ["weak", "no_etag"]:
            with self.subTest(mode=mode):
                report = self.scan(mode)
                self.assertEqual(report.verdict, "warn")
                self.assertEqual(report.exit_code(), 0)
                self.assertEqual(report.exit_code(strict=True), 1)
                self.assertIn("repeat", {p["name"] for p in report.probes})

    def test_end_ignored_is_warning(self):
        self.assertEqual(self.scan("ignores_end").verdict, "warn")

    def test_legal_partial_response_is_warning(self):
        self.assertEqual(self.scan("short_range").verdict, "warn")

    def test_unknown_total_is_incomplete(self):
        report = self.scan("unknown_size")
        self.assertEqual(report.verdict, "inconclusive")
        self.assertEqual(report.exit_code(), 2)

    def test_empty_is_inconclusive(self):
        self.assertEqual(self.scan(data=b"").verdict, "inconclusive")

    def test_one_byte_resource(self):
        self.assertEqual(self.scan(data=b"x").verdict, "pass")

    def test_http_error_is_inconclusive(self):
        self.assertEqual(self.scan("forbidden").verdict, "inconclusive")

    def test_connection_timeout(self):
        with fixture("timeout") as (url, _, _):
            report = diagnose(url, timeout=0.03, use_proxy=False)
        self.assertEqual(report.exit_code(), 2)
        self.assertEqual(report.probes[0]["error"], "connection_timeout")

    def test_transport_errors(self):
        for mode, error in [
            ("loop", "redirect_loop"),
            ("bad_redirect", "invalid_redirect_url"),
            ("no_location", "missing_or_duplicate_redirect_location"),
        ]:
            with self.subTest(mode=mode), fixture(mode) as (url, _, _):
                result = Transport(timeout=1, use_proxy=False).get(url, "bytes=0-1", 3)
                self.assertEqual(result.error, error)

    def test_cross_origin_strips_all_custom_headers(self):
        with fixture() as (target, calls, _), fixture("foreign") as (url, _, source):
            source.target = target
            response = Transport(
                headers={"Authorization": "secret", "X-Api-Key": "secret"}, use_proxy=False
            ).get(url, "bytes=0-1", 3)
        self.assertEqual(response.status, 206)
        self.assertNotIn("Authorization", calls[0])
        self.assertNotIn("X-Api-Key", calls[0])
        self.assertTrue(response.redirects[0]["custom_headers_stripped"])

    def test_same_origin_redirect_preserves_headers(self):
        with fixture("redirect") as (url, calls, _):
            result = Transport(headers={"Authorization": "secret"}, use_proxy=False).get(
                url, "bytes=0-1", 3
            )
        self.assertEqual(calls[1]["Authorization"], "secret")
        self.assertEqual(result.body, DATA[:2])
        self.assertNotIn("do-not-print", json.dumps(result.redirects))

    def test_read_cap_when_server_ignores_range(self):
        with fixture("ignored", data=b"x" * 1_000_000) as (url, _, _):
            response = Transport(use_proxy=False).get(url, "bytes=0-1", 3)
        self.assertEqual(len(response.body), 3)

    def test_identity_is_requested(self):
        with fixture() as (url, calls, _):
            diagnose(url, use_proxy=False)
        self.assertTrue(all(c["Accept-Encoding"] == "identity" for c in calls))

    def test_validate_options(self):
        for options in [
            {"sample_bytes": 1},
            {"sample_bytes": 65537},
            {"timeout": 0},
            {"timeout": float("nan")},
            {"timeout": float("inf")},
        ]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                diagnose("https://example.com/file", **options)

    def test_reject_invalid_urls(self):
        for url in [
            "file:///tmp/file",
            "https://u:p@example.com/f",
            "https://x:bad/f",
            "ftp://example.com/file",
            "https://example.com/\nheader",
            "no-scheme",
            "https://example.com/空格",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_reject_unsafe_headers(self):
        for header in [
            {"Range": "bytes=0-1"},
            {"X-Key": "secret\r\nInjected: yes"},
            {"Host": "elsewhere"},
            {"If-None-Match": "*"},
            {"bad name": "x"},
        ]:
            with self.subTest(header=header), self.assertRaises(ValueError):
                validate_headers(header)

    def test_redacts_query_and_fragment(self):
        self.assertEqual(
            redact_url("https://example.com/a?token=secret#private"),
            "https://example.com/a?REDACTED",
        )


class CLITests(unittest.TestCase):
    def test_offline_demo_does_not_resolve_hostname(self):
        with patch("socket.getfqdn", side_effect=AssertionError("No reverse DNS in offline demo")):
            with demo_server() as base:
                report = diagnose(base + "/good", use_proxy=False)
        self.assertEqual(report.verdict, "pass")

    def test_demo_exit_codes(self):
        for mode, expected in [("good", 0), ("broken", 1), ("ignores-range", 1)]:
            with self.subTest(mode=mode), contextlib.redirect_stdout(io.StringIO()) as output:
                code = main(["--demo", mode, "--json"])
                report = json.loads(output.getvalue())
                self.assertEqual(code, expected)
                self.assertEqual(report["exit_code"], expected)

    def test_json_does_not_leak_request_secrets(self):
        with fixture() as (url, calls, _), patch.dict(os.environ, {"TEST_AUTH": "Bearer hidden"}):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = main(
                    [
                        url + "?token=hidden#hidden",
                        "--json",
                        "--no-proxy",
                        "--header-env",
                        "Authorization=TEST_AUTH",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertTrue(all(c["Authorization"] == "Bearer hidden" for c in calls))
        self.assertNotIn("hidden", output.getvalue())

    def test_missing_env_does_not_leak_value(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(["https://example.com", "--header-env", "Authorization=MISSING"])
        self.assertEqual(caught.exception.code, 2)

    def test_output_failure_is_not_success(self):
        with patch("sys.stdout.write", side_effect=BrokenPipeError):
            self.assertEqual(main(["--demo", "good"]), 2)


if __name__ == "__main__":
    unittest.main()
