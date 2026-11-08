# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
semantic versioning.

## [1.0.0] - 2026-11-08

### Added

- Local-first YouTube discovery, enrichment, proximity scoring, and outlier
  analysis.
- Full channel audit with evidence-backed diagnosis, competitor practices,
  30-day strategy, and ten video ideas.
- Desktop application for channels, comparison, Studio analytics, notes,
  whiteboard, editorial plan, production estimates, research, hooks, ideas,
  and the AI brain.
- YouTube OAuth flow for private Studio analytics.
- CSV/Markdown reports and optional Gmail report delivery.
- Two-bucket quota accounting with Pacific-day boundaries.
- Pinned dependencies, lock file, public release build, CI, security policy,
  issue/PR templates, and an English user guide with PDF.

### Fixed during the public-release audit

- Atomic first-run copy of `config.yaml`; no overwrite and no leftover
  temporary file.
- Durable-run closure retries transient I/O errors, not only SQLite errors.
- An unreadable database is reported in the GUI instead of looking empty.
- Whiteboard, reports, and the scheduled `.bat` file are written atomically.
- LLM model listing accepts a JSON array as well as an object.
- Malformed Google error payloads no longer raise a secondary exception.
- `doctor --offline` is available as an explicit local diagnostic.
- Source archives exclude development caches and coverage artifacts.

### Known limitations

- The v1.0 desktop interface is French; the documentation is English.
- The Windows executable is not code-signed.
- Full Ruff and strict mypy are not yet green; CI gates only correctness rules.
- The GUI has limited automated coverage.
