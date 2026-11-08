# Troubleshooting

## The app does not start

1. Run `Outly.exe --help` in PowerShell to see whether the launcher works.
2. From source, run `python -m outly.gui` and read the traceback.
3. Check `data/interface-erreur.log` if the GUI created it.
4. Confirm that `config.yaml` is valid YAML and `.env` has no unquoted `#`.
5. Move the app to a short writable path such as `C:\Outly`.

## SmartScreen or antivirus warning

The executable is unsigned. Verify the SHA-256 from the release page, then use
**More info -> Run anyway** only if it matches. Add a folder exclusion only if
you understand the risk. Never disable protection system-wide.

## `pip install` fails with `OSError: [Errno 22]`

Retry with:

```powershell
python -m pip install --no-cache-dir --no-compile -r requirements.txt
python -m pip check
```

Use a short path, close editors that lock files, and check antivirus logs.

## YouTube returns 403 or quota errors

- Confirm the YouTube Data API v3 is enabled for the key's project.
- Check `python -m outly quota`; the Pacific quota day may have rolled over.
- `search.list` has a **separate** 100-call bucket. A normal run uses both
  buckets.
- Do not create multiple projects to bypass quota; this violates the Developer
  Policies.
- Wait for the reset or lower `max_search_calls_per_run`.

## No channels are found

- Check `seeds.txt` formats: `@handle`, `/@handle`, `/channel/UC...`, raw `UC...`,
  or `/user/name`.
- Custom `/c/...` URLs are not resolvable by the API.
- Keywords should be phrases your audience would search, not a paragraph.
- The subscriber and age filters may be too strict.
- Run `python -m outly discover --dry-run` to see the call plan.

## LLM errors

- Run `python -m outly doctor --probe` to list models.
- Model names change; copy the exact name from the provider console.
- A 429 means rate limiting: lower `llm.requests_per_minute` or wait.
- If the main provider fails, configure `LLM_FALLBACK_*`.
- Local Ollama must be running and the model must be pulled; the key may be
  empty.

## Research with AI fails

`--ia` requires `EXA_API_KEY`. Without it, use `--sans-ia`; Wikipedia-only mode
needs no LLM key and no Exa key. A claim is not "cross-checked" until three
independent recognized domains support it.

## Studio shows no private metrics

Private analytics require OAuth from the channel owner. Check:

1. YouTube Data API v3 and YouTube Analytics API are enabled.
2. The OAuth client is type **Desktop app**.
3. The consent screen includes the required scopes.
4. The refresh token exists in `.env` and has not been revoked.
5. Analytics reports can lag 24-48 hours; impressions may be unavailable.

## Email does not send

- `SMTP_PASSWORD` must be a Gmail **app password**, not the account password.
- Two-factor authentication must be enabled on the sender account.
- The sender is fixed by the app; only the recipient is configurable.
- Use `mail --test` and `mail --dry-run` first.
- An empty report is not sent by design.

## Database looks empty or corrupted

`PRAGMA quick_check` should return `ok`. Restore a backup made with SQLite's
backup API, not a copy taken while the app was writing. The GUI reports an
unreadable database instead of silently showing zero rows.

## Back up safely

```python
import sqlite3
from pathlib import Path

source = Path("data/radar.sqlite3")
target = Path("backup/radar-copy.sqlite3")
target.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
```

Verify with `PRAGMA quick_check` before relying on the copy.
