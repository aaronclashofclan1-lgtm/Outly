# Outly portable release

This folder is the no-install Windows build.

## Start

1. Keep `Outly.exe` in a writable folder.
2. Double-click it. On first launch it creates `config.yaml`, `positioning.md`,
   `seeds.txt`, `keywords.txt`, and `.env` here.
3. Open **Keys & model** in the app, or edit `.env` with a text editor.
4. Fill in your positioning, seeds, and keywords.
5. Run **Launch**.

Python is not required. The executable is unsigned; verify the SHA-256 on the
GitHub release before running it. Do not download it from an unknown mirror.

## Files

| File | Purpose |
|---|---|
| `Outly.exe` | Portable application |
| `.env.example` | Key template; copy to `.env` only in a private folder |
| `config.yaml` | Budgets and filters |
| `positioning.md` | Your channel positioning |
| `seeds.txt` | Known competitor channels |
| `keywords.txt` | Niche search phrases |
| `logo.png` | Window/application logo |
| `data/` | Created locally; database and preferences |
| `out/` | Created locally; reports |

Never upload a `data/` folder or a real `.env`. See
[`../docs/USER_GUIDE.md`](../docs/USER_GUIDE.md) and
[`../docs/INSTALL.md`](../docs/INSTALL.md).
