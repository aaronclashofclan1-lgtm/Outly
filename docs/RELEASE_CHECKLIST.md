# Release checklist

Run from the repository root on Windows.

## Before tagging

- [ ] `git status` is clean and no `.env`, database, log, or report is tracked.
- [ ] `python scripts/verify_public_repo.py .` passes.
- [ ] `python -m pytest -q` passes.
- [ ] `python -m ruff check . --select E9,F63,F7,F82` passes.
- [ ] `python -m pip check` passes.
- [ ] `python -m pip_audit -r requirements.txt` reports no known vulnerability.
- [ ] The version is updated in `pyproject.toml`, `outly/__init__.py`,
      `CHANGELOG.md`, and `CITATION.cff`.
- [ ] `docs/USER_GUIDE.md` and `docs/USER_GUIDE.pdf` match.
- [ ] `release/Outly.exe` is current and tested with `--help`, `quota`, and
      `doctor --offline`.
- [ ] The exe SHA-256 is recorded.
- [ ] No screenshot, seed, positioning, or report contains personal data.
- [ ] The README badges and links point to the real repository.

## Build

```powershell
python -m pip install -r requirements-dev.txt
.\scripts\build_windows.ps1
```

## Test the release payload

Run in a disposable folder:

```powershell
.\release\Outly.exe --help
.\release\Outly.exe quota
.\release\Outly.exe doctor --offline
```

Check that first launch creates config, templates, and `.env` placeholders.

## Tag and publish

```bash
git tag -a v1.0.0 -m "Outly 1.0.0"
git push origin v1.0.0
```

The GitHub workflow builds the Windows executable, creates a source archive,
and publishes a release directly. Review it immediately after the job finishes;
confirm that the assets are present and the CI job is green.

## After publishing

- Verify the release page links and downloads.
- Verify the asset hash locally.
- Announce the known limitations: French UI, unsigned exe, P3 lint/mypy debt.
- Monitor issues for accidental secret exposure and rotate credentials if
  needed.
