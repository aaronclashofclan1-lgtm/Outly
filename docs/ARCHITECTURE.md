# Architecture

## Shape

Outly is a local Python application with three surfaces over one data model:

1. a command-line interface (`outly/cli.py`);
2. a CustomTkinter desktop interface (`outly/gui/`);
3. a PyInstaller one-file launcher (`lanceur_exe.py`).

Both surfaces read `config.yaml`, `.env`, and the SQLite database in the project
root. The GUI runs CLI commands as child processes where possible, so a button
and a terminal command share the same validation and output path.

## Data flow

```text
seeds + keywords + my channel + featured channels + description links
        |
        v
discovery -> cheap filters -> candidate channels
        |
        v
enrichment (uploads playlist + videos.list) -> metrics
        |
        v
LLM proximity scoring -> retained channels -> report
        |
        +--> outlier analysis -> hooks / strategy / audit
        +--> notes / research / ideas / editorial plan
        +--> Studio OAuth analytics
```

## Modules

- `config.py`: `.env` and `config.yaml`, validation, root resolution.
- `db.py`: schema, migrations, upserts, reads, backup/restore helper.
- `quota.py`: two buckets, Pacific day, clean stop.
- `youtube_api.py`: HTTP client, retries, typed errors.
- `discovery.py`, `enrichment.py`, `metrics.py`: source and measurement layer.
- `llm/`: OpenAI-compatible client, prompts, schemas, scoring.
- `outliers.py`, `hooks.py`, `strategie.py`, `production.py`, `audit.py`:
  analysis and generation.
- `notes.py`, `recherche.py`, `idees.py`, `cerveau.py`, `plan.py`,
  `playlist.py`: creator workspace.
- `studio.py`, `historique.py`: OAuth analytics and competitor history.
- `emailing.py`, `planification.py`, `report.py`: delivery and exports.
- `gui/`: pages, shared data access, process runner, local preferences.

## Persistence

SQLite is the source of truth for channels, videos, metrics, scores, cache
fingerprints, notes, plan entries, research, ideas, playlist items, and quota
events. User text files remain the source of truth for positioning, seeds,
keywords, and thresholds. The GUI writes both, but never stores secrets in the
database.

API-derived data has a 30-day ceiling. The cache layer also enforces per-type
TTLs. `cache purge` deletes old rows; `cache clear` empties the cache tables.

## Network boundary

- YouTube Data API v3: public discovery and public metrics.
- YouTube Analytics API: private analytics after OAuth consent.
- Configured LLM endpoint: OpenAI-compatible chat completions.
- Exa: optional web excerpts for research.
- Gmail SMTP: optional report delivery.
- MediaWiki: optional Wikipedia research.

No request is sent to an unconfigured endpoint. There is no telemetry service.

## OAuth

Studio connection opens a loopback HTTP server on `127.0.0.1`, builds a Google
authorization URL with a state value, waits for the callback, exchanges the code,
and stores the refresh token in `.env`. Credentials are never written into
reports, logs, or the database.

## Build

`Outly.spec` collects the package data (SQL schema, JSON schemas, templates,
icons, guides, tzdata, CustomTkinter assets). `scripts/build_windows.ps1`
prepares icon/version metadata and runs PyInstaller. The released executable is
one file and does not require Python on the target machine.

## Design constraints

- Windows-first; no native Linux/macOS packaging is claimed.
- One local user; not a multi-tenant service.
- API quota and retention rules are product constraints, not optional settings.
- The French desktop UI is v1.0; documentation and public metadata are English.
