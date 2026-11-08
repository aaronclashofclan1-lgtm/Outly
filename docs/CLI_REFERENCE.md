# CLI reference

Run from the repository root, or use the installed `outly` entry point.

## Global

```text
outly --help
outly --version
outly doctor [--probe | --offline]
outly quota
outly show <id|@handle|title>
outly cache {purge,clear} [--older-than DAYS]
```

## Commands

| Command | Purpose |
|---|---|
| `init` | Create missing starter files without overwriting |
| `doctor` | Validate config, dependencies, files, and keys |
| `discover` | Find candidate channels |
| `enrich` | Fetch latest uploads and metrics |
| `score` | Score niche proximity with the LLM |
| `analyze-outliers` | Analyze the strongest outlier videos |
| `hooks` | Analyze outlier intros and write opening variants |
| `brain` | Connect notes, outliers, research, ideas, and the plan |
| `idees` | Generate video ideas with three distinct titles |
| `recherche` | Wikipedia-only or Wikipedia + Exa research |
| `audit` | Full channel audit and 30-day strategy |
| `strategie` | Diagnose recent videos and propose tests |
| `production` | Estimate time, gear, footage, and budget |
| `report` | Write `channels.csv`, `outliers.csv`, `report.md` |
| `mail` | Send the report by email |
| `run` | Discover, enrich, score, and report |

## Common examples

```bash
python -m outly doctor --offline
python -m outly doctor --probe
python -m outly quota
python -m outly discover --dry-run
python -m outly run --with-outliers --transcripts
python -m outly audit --force
python -m outly strategie --jours 10
python -m outly production --plan 3
python -m outly report
python -m outly mail --dry-run
python -m outly mail --test
python -m outly mail --si-du
python -m outly mail --sans-veille
```

## Hooks

```bash
python -m outly hooks --sujet "Why most beginners quit" --transcripts
python -m outly hooks --force
```

Transcript reading must be enabled. Without a transcript the command stops with
a clear message rather than inventing content. `--force` ignores a cached set.

## Brain and ideas

```bash
python -m outly brain --question "What angles for a beginner series?"
python -m outly brain --inclure-archives --force
python -m outly idees --nombre 5
python -m outly idees --question "A series for beginners who quit"
python -m outly idees --force
```

Results are cached by a fingerprint of notes, research, ideas, the editorial
plan, the watchlist, positioning, outliers, and the question. Changing that
material invalidates the relevant cache.

## Research

```bash
python -m outly recherche --titre "Urban heat" --mots-cles "heat island, trees" --sans-ia
python -m outly recherche --titre "Urban heat" --mots-cles "heat island" --ia
python -m outly recherche --titre "Urban heat" --mots-cles "heat island" --ia --force
```

`--sans-ia` uses the MediaWiki API only. `--ia` adds Exa and an LLM synthesis,
and requires `EXA_API_KEY`. A claim is marked "cross-checked" only when at least
three independent, recognized web domains support it; Wikipedia is a starting
point, not one of the three. Otherwise it is shown as "to verify". Disagreements
are preserved.

## `run` options

```text
--dry-run                 Show the plan and estimate without spending
--max-searches N          Override the search-call budget
--limit N                 Enriched channels per step
--limit-score N           Channels scored by the LLM
--with-outliers           Add outlier analysis
--transcripts             Enable transcript reading for hooks
```

## `mail` options

```text
--si-du        Send only when the configured frequency is due
--test         Mark the message [test], do not alter the schedule
--dry-run      Compose without sending
--sans-veille  Send the current database report without a new watch run
```

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 2 | Invalid configuration or rejected key |
| 3 | Quota budget reached; clean stop, partial report possible |
| 4 | LLM unavailable |
| 5 | No usable data |
| 6 | Email delivery or SMTP configuration failure |

## Environment

`OUTLY_ROOT` points the CLI at another project folder. The GUI accepts
`--racine` and passes the same root to child commands. `NO_COLOR` and the usual
terminal settings are respected by the CLI text output.
