"""PyInstaller entry point; package-relative imports stay inside rangeglass."""

from rangeglass.cli import main

raise SystemExit(main())
