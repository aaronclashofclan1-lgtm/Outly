# Privacy

Outly is designed as a local desktop tool. It does not create an Outly account,
does not include analytics, and does not send usage events to the maintainers.

## Data stored on your computer

- `data/radar.sqlite3`: channels, videos, public metrics, scores, cache
  fingerprints, notes, plan entries, ideas, research, playlist, and quota rows.
- `data/preferences.json`: theme, layout, and interface preferences.
- `data/studio.json`: cached YouTube Studio analytics when OAuth is connected.
- `data/tableau-blanc.json`: whiteboard content.
- `out/`: generated CSV and Markdown reports.
- `.env`: API keys, OAuth credentials, refresh token, and Gmail app password.
- `config.yaml`, `positioning.md`, `seeds.txt`, `keywords.txt`: your settings and
  research material.

Anyone with access to the folder can read these files. Use Windows account
permissions, disk encryption, and a private folder. Do not sync a folder that
contains `.env` to a public cloud drive.

## Network requests

Outly contacts only services you configure:

| Service | Data sent | Purpose |
|---|---|---|
| YouTube Data API v3 | Channel/video IDs, search terms | Public discovery and metrics |
| YouTube Analytics API | OAuth token, channel/report IDs | Private Studio analytics |
| LLM endpoint | Positioning, channel/video summaries, notes | Scoring and generation |
| Exa | Research queries | Optional web excerpts |
| MediaWiki | Research keywords | Optional Wikipedia lookup |
| Gmail SMTP | Report body and CSV attachments | Optional email delivery |

There is no request to a maintainer-controlled server. Provider privacy terms
apply to their services. Do not send confidential material to a third-party LLM
unless you accept its retention policy.

## YouTube API data

The YouTube Developer Policies require refreshing or deleting API-derived data
within 30 days. Outly caps its cache at 30 days and provides
`python -m outly cache purge`. Reports and the local database remain under your
control. Do not publish an API-derived dataset as if it were official YouTube
data.

## Secrets

Keys are stored only in `.env`, which is ignored by Git, excluded from reports,
and never written to the database. The GUI masks them. The OAuth refresh token
is stored in `.env`; revoke access from your Google account if the file is lost.

## Children

Outly is a professional/content-research tool, not directed at children. Do not
use it to collect personal data about minors.

## Deletion

Close the app and delete its folder, plus `%LOCALAPPDATA%\Outly` if it was used.
Revoke API keys, OAuth access, and the Gmail app password at the provider. A
backup you made elsewhere is not deleted automatically.
