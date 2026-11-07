# Why a small HTTP range diagnostic?

Research snapshot: **2026-11-07**. This is a scoped opportunity assessment, not proof of demand or a claim of being the first implementation.

## Recent primary evidence

| Source | Observed need | What Rangeglass covers |
| --- | --- | --- |
| [GitLab Runner MR !7635](https://gitlab.com/gitlab-org/gitlab-runner/-/merge_requests/7635), opened 2026-10-13 | Parallel cache/artifact downloads accepted full or mismatched-range responses, producing corrupt files. | Actual nonzero range probes, status/range/length checks. |
| [Magda issue #3673](https://github.com/magda-io/magda/issues/3673), opened 2026-07-03, completed July 5 | Large downloads restarted from zero because the download route ignored Range. | Unsupported-range diagnosis with a small read cap. |
| [Cloudflare: Origin Range Requests](https://blog.cloudflare.com/origin-range-requests/), 2026-10-23 | Origin and edge range handling has operational complexity and bandwidth tradeoffs. | Endpoint observations; explicitly does not measure or bound origin fetch traffic. |
| [uv issue #18642](https://github.com/astral-sh/uv/issues/18642), opened 2026-03-22 | Users request interrupted-download resume. | Helps investigate the server side; does not implement uv resume. |
| [Hugging Face dataset issue #7931](https://github.com/huggingface/datasets/issues/7931), opened 2026-01-03 | Redirected dataset links broke browser range workflows through CORS. | Redirect/range evidence only; browser CORS is outside v0.1. |

## Community context

The search also covered Reddit, Hacker News, and Hugging Face's public forum. The [Hugging Face forum discussion](https://discuss.huggingface.co/t/cors-support-for-http-range-requests-on-dataset-files/172172) describes a real range/redirect debugging workflow. The [Reddit download-interruption thread](https://www.reddit.com/r/OperaGX/comments/16agdrd/) illustrates older user confusion about restart versus resume; it is background, not a fresh 2026 demand signal.

The [Ask HN developer-tool discussion](https://news.ycombinator.com/item?id=46345827) was useful for broad exploration, but does not establish demand for this particular tool. The stronger evidence here is concrete bug reports and feature requests.

## Alternatives and the narrower opportunity

- **curl / HTTPie:** excellent raw requests and manual investigation. Rangeglass assembles several requests, correlates overlapping bytes and validators, and emits one diagnostic report.
- **wget / aria2 / download libraries:** mature transfer/resume tools. Building another downloader would add scope without a clear advantage.
- **[badhttp](https://badhttp.dev/):** a substantial test service for exercising HTTP clients. Rangeglass instead probes a user-supplied endpoint. Our offline fixtures are small original tests, not a copy of that service.
- **HTTP range parsers/readers:** GitHub searches found many libraries that parse or consume ranges. We did not establish that endpoint diagnostic tools are absent; the opportunity is a convenient, bounded, well-documented workflow.

## Candidates rejected

Cross-platform filename checking had clear current issues, but existing tools already cover much of it: [portable-path-audit](https://github.com/7364588/portable-path-audit), [portacheck](https://pypi.org/project/portacheck/), and [pre-commit-hooks](https://github.com/pre-commit/pre-commit-hooks).

ZIP filename repair is served by focused projects including [zipkirei](https://github.com/yuk7/zipkirei) and [zifu](https://github.com/tats-u/zifu). Offline model-weight preflight is also already covered by [weightlint](https://github.com/thisbejim/weightlint) and [ckptkit](https://github.com/stef41/ckptkit). These were less attractive for another small first release.

## Standards, not heuristics alone

The behavior is based on [RFC 9110 sections 13.1.5, 14, and 15.3.7](https://www.rfc-editor.org/rfc/rfc9110.html). Missing `Accept-Ranges` is not a failure. A server may ignore a range; we call that unsupported for the tested workflow, not a protocol vulnerability. Weak ETags are not sent as matching `If-Range` validators.

The product hypothesis is modest: maintainers and people debugging large downloads may prefer a small report to a sequence of manual commands. Usage and issue reports must validate that hypothesis. No adoption, star-count, performance, or uniqueness claims are implied.
