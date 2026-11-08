# Installation

Outly is a Windows desktop application. It can run as the portable
`Outly.exe` or from Python 3.11+.

## Option A: portable executable

1. Download `Outly.exe` from `release/` or GitHub Releases.
2. Create a writable folder for the app, for example `C:\Outly`.
3. Put `Outly.exe` in that folder and double-click it.
4. On first launch Outly creates its local files:
   `config.yaml`, `positioning.md`, `seeds.txt`, `keywords.txt`, and `.env`.
5. Open **Settings -> Keys & model** and add the keys you need.
6. Fill in your positioning, seeds, and keywords.
7. Run `Outly.exe doctor --offline` from PowerShell if you want a local check.

The folder is portable: copy the whole folder to move your configuration,
database, and reports. If the folder is not writable, Outly falls back to
`%LOCALAPPDATA%\Outly`.

> The executable is unsigned. SmartScreen may warn you. Verify the SHA-256
> published beside the release before choosing **More info -> Run anyway**.

## Option B: from source

Requirements: Windows 10/11, Python 3.11 to 3.13, and a terminal.

```powershell
git clone https://github.com/aaronclashofclan1-lgtm/Outly.git
cd outly
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m outly init
python -m outly doctor --offline
```

Start the GUI:

```powershell
python -m outly.gui
```

Or double-click `lancer-interface.bat`; it uses `.venv\Scripts\pythonw.exe`
when available.

For an editable development install:

```powershell
python -m pip install -e ".[dev]"
```

## Option C: exact audit environment

`requirements.lock` contains the full resolved runtime set validated for
v1.0.0:

```powershell
python -m pip install -r requirements.lock
```

Use it when you need the exact tested versions rather than the direct pins.

## Cold-install troubleshooting

On some Windows machines, antivirus or a network filter can interrupt pip while
it compiles or locks a `.pyc` file:

```text
OSError: [Errno 22] Invalid argument
```

Retry without bytecode compilation and without the cache:

```powershell
python -m pip install --no-cache-dir --no-compile -r requirements.txt
python -m pip check
```

This was observed once on the audit machine; the retry completed and all 365
tests passed. If the error persists, exclude the project folder from real-time
antivirus scanning temporarily, use a short path such as `C:\Outly`, and retry.

## Keys and permissions

| Service | Required? | Where it is used |
|---|---|---|
| YouTube Data API v3 key | Yes, for discovery | Public channels and videos |
| OpenAI-compatible LLM key | For scoring and generation | Proximity, audit, hooks, ideas |
| Exa key | Optional | Research with AI |
| YouTube OAuth client | Optional | Private Studio analytics |
| Gmail app password | Optional | Email reports |

Enable **YouTube Data API v3** in Google Cloud Console, create an API key, and
restrict it to that API. For Studio analytics, also enable **YouTube Analytics
API** and create a **Desktop OAuth client**. For Gmail, enable two-factor
authentication and create an app password. Full provider steps are in
[`guides/`](../guides/README.md).

Never commit `.env`, never paste a key into an issue, and never place a real
`.env` in a public archive.

## Build the Windows executable

```powershell
python -m pip install -r requirements-dev.txt
.\scripts\build_windows.ps1
```

The script:

1. creates `build/outly.ico` and `build/version-outly.txt`;
2. runs PyInstaller with `Outly.spec`;
3. copies `dist/Outly.exe` to `release/Outly.exe`;
4. prints the SHA-256.

Test the result:

```powershell
.\release\Outly.exe --help
.\release\Outly.exe quota
.\release\Outly.exe doctor --offline
```

The first command may create local config files in the current directory. Run
it in a disposable folder when you do not want that.

## Updating

- Executable: replace `Outly.exe`; keep `config.yaml`, `.env`, and `data/`.
- Source: pull the new source, update the virtual environment, run
  `python -m outly doctor --offline`, then run the tests.
- Database migrations run automatically and are additive. Back up
  `data/radar.sqlite3` before a major upgrade.

## Uninstall

Close Outly, then delete the application folder. If it was not writable, also
delete `%LOCALAPPDATA%\Outly`. This removes the app, local database, reports,
and `.env`. Revoke API keys and the Gmail app password separately at the
provider.
