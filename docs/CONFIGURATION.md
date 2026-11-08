# Configuration reference

Outly reads `config.yaml` and `.env` from its root folder. `outly init` creates
missing files without overwriting existing ones. The GUI edits the same files;
comments in `config.yaml` are preserved when possible.

## `config.yaml`

### `youtube`

| Key | Default | Meaning |
|---|---:|---|
| `region_code` | `FR` | First search pass region |
| `relevance_language` | `fr` | First pass language; global pass omits it |
| `batch_size` | `50` | IDs per API call |
| `videos_per_channel` | `30` | Latest videos analyzed |
| `max_search_calls_per_run` | `20` | `search.list` calls per run |
| `max_search_calls_per_day` | `90` | Daily search-call ceiling |
| `max_units_per_run` | `2000` | Main-bucket units per run |
| `max_units_per_day` | `8000` | Daily main-bucket ceiling |
| `subscriber_min` | `1000` | Ignore smaller channels |
| `subscriber_max` | `5000000` | Ignore larger channels |
| `min_channel_age_days` | `180` | Ignore newer channels |
| `max_candidates_per_run` | `400` | Candidate cap |
| `analysis_limit` | `60` | Channels enriched per run |

`search.list` and all other endpoints are separate buckets. The app stops
cleanly when either run or daily budget is reached. The day boundary is Pacific
time, matching Google's quota day.

### `moi`

| Key | Default | Meaning |
|---|---|---|
| `chaine` | empty | Your `@handle`, URL, or `UC...` ID |
| `recherche_internationale` | `true` | Run each query in France, then worldwide |

The channel in `moi.chaine` is never treated as a competitor.

### `discovery`

| Key | Default | Meaning |
|---|---|---|
| `use_channel_sections` | `true` | Read featured channels of seeds |
| `use_description_links` | `true` | Reuse `@handles` found in descriptions |
| `require_keyword_hit` | `false` | Require a keyword hit before keeping |

### `cache`

| Key | Default | Meaning |
|---|---:|---|
| `ttl_channel_days` | `7` | Channel cache lifetime |
| `ttl_videos_days` | `1` | Video cache lifetime |
| `ttl_search_days` | `3` | Search cache lifetime |
| `max_ttl_days` | `30` | Hard ceiling required by the API policy |

### `metrics`, `llm`, `classement`, `outliers`

`short_max_seconds: 180` is the Shorts cutoff. `min_videos_for_metrics: 3`
marks smaller samples as insufficient. `llm` controls channel cap, temperature,
timeout, retries, rate, JSON mode, retention score, and fallback. `classement`
sets the minimum retained channels and the local proximity threshold.
`outliers` controls how many channels/videos are analyzed and the transcript
excerpt size.

### `transcripts`

`enabled: false` by default. The transcript reader uses an unofficial YouTube
interface and may be blocked; every failure is logged and non-fatal. Use it only
where you accept that risk and comply with the Developer Policies.

### `hooks`

`enabled` triggers automatic generation after a full run. `sujet`, `variants`,
`max_videos`, and `max_chars_transcription` control the result.

### `output`

`dir: out`, `csv_separator: ";"`, `csv_encoding: utf-8-sig`, and
`report_top: 10`. The BOM and semicolon are intentional for French Excel; switch
to `,` for international locales.

### `email`

`destinataire`, `frequence` (`jamais`, `quotidien`, `hebdomadaire`, `mensuel`),
`heure`, `jour_semaine` (1-7), `jour_mois` (1-28), and `joindre_csv`. The sender
is fixed by the app. Only the Gmail app password belongs in `.env`.

## `.env`

` .env` is secret and ignored by Git. Supported keys:

```text
YOUTUBE_API_KEY
LLM_BASE_URL
LLM_MODEL
LLM_API_KEY
LLM_FALLBACK_BASE_URL
LLM_FALLBACK_MODEL
LLM_FALLBACK_API_KEY
EXA_API_KEY
YOUTUBE_OAUTH_CLIENT_ID
YOUTUBE_OAUTH_CLIENT_SECRET
YOUTUBE_REFRESH_TOKEN
SMTP_PASSWORD
```

Values with `replace_with_...`, `collez_ici...`, or an empty value are treated
as absent. The UI masks secrets and never writes them into reports.

## Files created at first run

| File | Purpose |
|---|---|
| `positioning.md` | Audience, promise, tone, format, differentiation, exclusions |
| `seeds.txt` | Known competitor handles, URLs, or IDs |
| `keywords.txt` | Niche search expressions |
| `config.yaml` | Thresholds and budgets |
| `.env` | Keys and optional integrations |

## Validation

```powershell
python -m outly doctor --offline
python -m outly doctor --probe
```

`--offline` checks local files and dependencies without network calls.
`--probe` makes one real YouTube call and lists available LLM models. Invalid
values are rejected before a run writes anything.
