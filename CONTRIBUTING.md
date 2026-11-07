# Contributing

Small, reproducible fixes are welcome. Rangeglass aims to remain a focused endpoint diagnostic with no runtime dependencies.

For bug reports, include the version, operating system, Python version, expected behavior, and a redacted report or a local reproducer. Review hostnames and URL paths before sharing. Never post access tokens or private download links.

```sh
python -m pip install -e .
python -m pip install ruff
python -m unittest discover -s tests -t . -v
ruff check .
ruff format --check .
```

Tests must use local fixtures and work without public internet access. A change to HTTP interpretation should cite the relevant standard and include a fixture showing the before/after behavior. Avoid tests that depend on a third-party server staying unchanged.

Keep the English and Chinese quickstarts aligned. Add or explain report codes in `docs/report-format.md`. Preserve the JSON schema contract; update the schema version for breaking changes.

By contributing, you agree that your contribution is distributed under the project's MIT license.
