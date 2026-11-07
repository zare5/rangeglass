# Changelog

## 0.2.0 — 2026-11-07

- Validate full `If-Range` fallback samples: reject empty/short bodies, changed lengths or ETags, and unexpected content encoding.
- Stop on HTTP access, rate-limit, and server errors. Report incomplete checks separately from observed protocol/content failures; preserve earlier failures if a later request fails.
- Reject partial content beyond the observed end and detect total-size changes in 416 responses.
- Accept valid contained range subsets with a warning, in accordance with RFC 9110.
- When suffix syntax returns 501, check explicit tail offsets and retain a warning; the default total read cap is now 8,200 bytes for up to eight probes.
- Add `--markdown` for reports that can be pasted into issues; contain untrusted text inside a literal code block.
- Add finding categories without changing JSON schema version 1; fix redirect-hop reporting and validate Python API option types.
- Expand regression tests, practical troubleshooting examples, audience guidance, and English/Chinese FAQs.

## 0.1.0 — 2026-11-07

First public release.

- Seven bounded HTTP probes for range support, overlapping bytes, total size, strong ETags, conditional resume, and end boundaries.
- Text and JSON reports with stable finding codes and actionable hints.
- Query-string redaction, environment-sourced authentication headers, explicit redirect handling, and cross-origin header stripping.
- Offline good, broken, and ignored-range demos.
- Python wheel, source distribution, portable zipapp, and Windows x64 console executable.
- Local HTTP regression fixtures and a Linux/macOS/Windows CI matrix.
