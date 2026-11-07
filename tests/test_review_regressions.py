"""Cases found by reviewing the first release, reproduced with real local HTTP replies."""

import contextlib
import io
import unittest
from email.message import Message
from unittest.mock import MagicMock, patch

from rangeglass.cli import format_markdown, main
from rangeglass.core import Report, diagnose
from rangeglass.transport import Transport

from .servers import fixture


class ReviewRegressions(unittest.TestCase):
    def scan(self, mode):
        with fixture(mode) as (url, calls, _):
            report = diagnose(url, sample_bytes=64, timeout=1, use_proxy=False)
        return report, calls

    def test_invalid_full_fallback_cannot_pass(self):
        for mode in [
            "fallback_empty",
            "fallback_short",
            "fallback_encoded",
            "fallback_length",
            "fallback_etag",
        ]:
            with self.subTest(mode=mode):
                report, _ = self.scan(mode)
                self.assertEqual(report.verdict, "fail", report.to_dict())

    def test_http_failures_are_inconclusive_and_stop(self):
        for mode, expected_probes in [
            ("late_403", 2),
            ("late_429", 2),
            ("fallback_503", 6),
            ("fallback_204", 6),
        ]:
            with self.subTest(mode=mode):
                report, calls = self.scan(mode)
                self.assertEqual(report.verdict, "inconclusive")
                self.assertEqual(len(calls), expected_probes)

    def test_past_end_partial_content_is_failure(self):
        report, _ = self.scan("past_end_206")
        self.assertEqual(report.verdict, "fail")

    def test_past_end_size_change_is_failure(self):
        report, _ = self.scan("past_end_size")
        self.assertEqual(report.verdict, "fail")

    def test_suffix_not_implemented_checks_explicit_tail(self):
        report, calls = self.scan("suffix_501")
        self.assertEqual(report.verdict, "warn")
        self.assertEqual(len(calls), 8)
        self.assertLessEqual(report.bytes_read, 8 * 65)
        self.assertEqual(calls[4]["Range"], "bytes=32704-32767")
        self.assertIn("suffix_unsupported", {f.code for f in report.findings})

    def test_suffix_fallback_does_not_hide_wrong_tail(self):
        report, _ = self.scan("suffix_501_bad_tail")
        self.assertEqual(report.verdict, "fail")
        self.assertTrue(
            any(f.probe == "explicit_tail" and f.code == "wrong_range" for f in report.findings)
        )

    def test_valid_contained_subset_is_a_warning(self):
        report, _ = self.scan("partial_subset")
        self.assertEqual(report.verdict, "warn")

    def test_observed_wrong_range_survives_later_http_failure(self):
        report, calls = self.scan("wrong_then_503")
        self.assertEqual(report.verdict, "fail")
        self.assertEqual(len(calls), 3)
        self.assertIn("wrong_range", {finding.code for finding in report.findings})

    def test_existing_failure_is_not_hidden_by_network_timeout(self):
        original = Transport.get

        def timeout_third(transport, url, range_value, cap, if_range=None):
            from rangeglass.transport import Response

            if range_value == "bytes=16384-16447":
                return Response(url=url, error="connection_timeout")
            return original(transport, url, range_value, cap, if_range)

        with patch.object(Transport, "get", timeout_third):
            report, _ = self.scan("wrong_offset")
        self.assertEqual(report.verdict, "fail")
        self.assertIn("connection_timeout", {f.code for f in report.findings})

    def test_https_downgrade_never_sends_second_request(self):
        transport = Transport(use_proxy=False)
        response = MagicMock()
        response.status = 302
        response.headers = Message()
        response.headers["Location"] = "http://example.com/unsafe"
        response.__enter__.return_value = response
        with patch.object(transport.opener, "open", return_value=response) as opened:
            result = transport.get("https://example.com/file", "bytes=0-1", 3)
        self.assertEqual(result.error, "https_downgrade_blocked")
        self.assertEqual(opened.call_count, 1)
        response.read1.assert_not_called()

    def test_redirect_limit_counts_only_followed_hops(self):
        with fixture("redirect_chain") as (url, calls, _):
            result = Transport(use_proxy=False).get(url, "bytes=0-1", 3)
        self.assertEqual(result.error, "too_many_redirects")
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(result.redirects), 5)
        self.assertEqual(result.body, b"")

    def test_markdown_export_runs_from_cli(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["--demo", "good", "--markdown"])
        self.assertEqual(code, 0)
        self.assertTrue(output.getvalue().startswith("### Rangeglass diagnostic"))
        self.assertIn("PASS -", output.getvalue())

    def test_markdown_contains_untrusted_backticks(self):
        report = Report("https://example.com/````<script>", 1024)
        report.finish()
        markdown = format_markdown(report)
        self.assertIn("\n`````text\n", markdown)
        self.assertTrue(markdown.endswith("\n`````\n"))

    def test_output_formats_are_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["--demo", "good", "--json", "--markdown"])
        self.assertEqual(caught.exception.code, 2)

    def test_api_rejects_non_integral_sample_and_non_numeric_timeout(self):
        for options in [{"sample_bytes": 2.5}, {"sample_bytes": "64"}, {"timeout": "10"}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                diagnose("https://example.com/file", **options)


if __name__ == "__main__":
    unittest.main()
