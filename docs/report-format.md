# Report format (schema version 1)

`rangeglass URL --json` writes one JSON object to stdout. Argument errors use stderr and exit 2; they do not produce a JSON report. Network/HTTP access failures produce an inconclusive report unless a protocol/content failure has already been observed; that remains a failure. Reports are observations, not resumability certificates. `--markdown` is an alternative human-readable export and is mutually exclusive with `--json`.

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`. Consumers should check it before parsing. |
| `version` | Rangeglass version. |
| `url` | Original URL, with query redacted and fragment removed. |
| `sample_bytes` | Requested maximum sample size. |
| `verdict` | `pass`, `warn`, `fail`, `unsupported`, or `inconclusive`. |
| `size_bytes` | Complete resource length, or `null`. |
| `validator` | Initial ETag category: `strong`, `weak`, `missing`, or `invalid`. |
| `bytes_read` | Sum of application body bytes read, excluding headers and redirects. |
| `probes` | Ordered observations; seven core probes, optionally eight with `explicit_tail`, or fewer if stopped early. |
| `findings` | Findings with `level`, `code`, `probe`, `message`, `hint`, and `category`. |
| `exit_code` | Exit code for this invocation, including the effect of `--strict`. |

Each probe includes `name`, `range`, `if_range` (`matching`, `mismatching`, or `null`), `status`, selected response `headers`, `etag_kind`, `bytes_read`, final redacted `url`, `redirects`, and `error`. No body bytes, ETag values, or custom request-header values are emitted. `headers` contains only range, length, encoding, and content-type metadata.

Redirect entries include `status`, redacted `from` and `to`, and `custom_headers_stripped`. URL paths remain visible. Error strings are controlled codes rather than raw exceptions, which can contain signed URLs.

`category`, added in v0.2.0, distinguishes `protocol` (response/content checks), `operational` (a request could not be completed), and `capability` (initial range support was absent). An error in the protocol category takes precedence over an operational failure when choosing the verdict. Clients consuming older v0.1 reports must allow this field to be absent.

Use `code`, not English message text, for automation. Codes include:

- Response findings: `range_ignored`, `unexpected_status`, `encoded_response`, `invalid_content_range`, `wrong_range`, `partial_range`, `wrong_content_length`, `wrong_body_length`, `duplicate_headers`, `ambiguous_framing`.
- Consistency findings: `size_changed`, `etag_changed`, `bytes_changed`, `if_range_ignored`, `full_length_changed`, `past_end_partial`.
- Caveats/incomplete checks: `empty_resource`, `unknown_size`, `no_strong_etag`, `if_range_unverified`, `end_boundary_unverified`, `suffix_unsupported`.
- Request/transport errors: `http_error`, `connection_timeout`, `connection_error`, `tls_error`, `body_timeout`, `body_read_error`, `incomplete_body`, `redirect_loop`, `too_many_redirects`, `missing_or_duplicate_redirect_location`, `invalid_redirect_url`, `https_downgrade_blocked`. `incomplete_body` is a protocol finding because a framed body was truncated; a timeout is operational.

Clients should tolerate additional codes, probe names, and object fields within this schema version. Core probes retain their relative order, but an `explicit_tail` probe can appear after `suffix`; incomplete scans omit later probes. Durations and random validator values are deliberately absent from reports. URLs, observed headers, and server behavior can still vary between runs.

Generate complete sample reports locally:

```sh
rangeglass --demo good --json
rangeglass --demo broken --json
```
