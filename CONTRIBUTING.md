# Contributing to Outly

Thanks for helping. Outly is a local-first Windows/Python application with a
large GUI, a CLI, a SQLite data layer, and several optional integrations.

## Before you start

- Read the [user guide](docs/USER_GUIDE.md), [architecture](docs/ARCHITECTURE.md),
  and [security policy](SECURITY.md).
- Search existing issues before opening a new one.
- Use the issue templates. Include the Outly version, Windows version, Python
  version when running from source, the exact command, and a redacted log.
- **Never paste** API keys, OAuth tokens, SMTP passwords, `.env` contents,
  personal databases, or private channel exports. Redact them first.

## Development setup

```powershell
git clone https://github.com/aaronclashofclan1-lgtm/Outly.git
cd outly
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m outly init
python -m outly doctor --offline
```

Run the tests before and after a change:

```powershell
python -m pytest -q
```

The suite must not make real network calls. Use the existing fake transports,
temporary databases, and frozen clocks.

## Pull requests

1. Keep the change focused. Do not mix unrelated refactors with a bug fix.
2. Add or update a regression test for a bug that can be reproduced.
3. Preserve existing assertions. Do not weaken a test, swallow an error, or add
   a blanket suppression to make CI green.
4. Update the changelog for user-visible changes.
5. Update the relevant English documentation and the PDF source.
6. Run the checks below.
7. Describe the user impact, tests run, and any remaining limitation.

```powershell
python -m pytest -q
python -m ruff check . --select E9,F63,F7,F82
python -m pip check
```

## Code conventions

- Python 3.11+, type hints where they improve clarity.
- Keep the existing French UI strings unless the change is explicitly a
  localization project. Documentation and code comments are English.
- Use atomic file writes for user data and reports.
- Treat every API response and every user file as untrusted input.
- Do not add network calls to tests.
- Do not commit `data/`, `out/`, `build/`, `dist/`, real `.env` files, or
  personal release builds.

## Documentation

- English is the repository language.
- The application UI is French in v1.0; use the label map in the user guide.
- Keep examples free of real channel names, keys, and private analytics.

## Security and license

Do not open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md).
By contributing, you agree that your contribution is licensed under MIT.
