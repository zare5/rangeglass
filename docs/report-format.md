# Report format (schema version 1)

`rangeglass URL --json` writes one JSON object to stdout. Argument errors use stderr and exit 2; they do not produce a JSON report. Network failures produce an inconclusive report when possible. Reports are observations, not resumability certificates.

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
| `probes` | Ordered observations; fewer than seven if the scan ended early. |
| `findings` | Findings with `level`, `code`, `probe`, `message`, and `hint`. |
| `exit_code` | Exit code for this invocation, including the effect of `--strict`. |

Each probe includes `name`, `range`, `if_range` (`matching`, `mismatching`, or `null`), `status`, selected response `headers`, `etag_kind`, `bytes_read`, final redacted `url`, `redirects`, and `error`. No body bytes, ETag values, or custom request-header values are emitted. `headers` contains only range, length, encoding, and content-type metadata.

Redirect entries include `status`, redacted `from` and `to`, and `custom_headers_stripped`. URL paths remain visible. Error strings are controlled codes rather than raw exceptions, which can contain signed URLs.

Use `code`, not English message text, for automation. Codes in v0.1 include:

- Response findings: `range_ignored`, `unexpected_status`, `encoded_response`, `invalid_content_range`, `wrong_range`, `partial_range`, `wrong_content_length`, `wrong_body_length`, `duplicate_headers`, `ambiguous_framing`.
- Consistency findings: `size_changed`, `etag_changed`, `bytes_changed`, `if_range_ignored`.
- Caveats: `empty_resource`, `unknown_size`, `no_strong_etag`, `if_range_unverified`, `end_boundary_unverified`.
- Operational errors: `connection_timeout`, `connection_error`, `tls_error`, `body_timeout`, `body_read_error`, `incomplete_body`, `redirect_loop`, `too_many_redirects`, `missing_or_duplicate_redirect_location`, `invalid_redirect_url`, `https_downgrade_blocked`.

Clients should tolerate additional codes and object fields within this schema version. Probe order is stable but incomplete scans omit later probes. Durations and random validator values are deliberately absent from reports. URLs, observed headers, and server behavior can still vary between runs.

Generate complete sample reports locally:

```sh
rangeglass --demo good --json
rangeglass --demo broken --json
```
