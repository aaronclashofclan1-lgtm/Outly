# Publishing this repository on GitHub

This repository is published at
https://github.com/aaronclashofclan1-lgtm/Outly.

The steps below document the initial publication; normal commits do not need
them.

## 1. Create the repository

1. Create an empty GitHub repository named `Outly`.
2. Do not add a README, license, or `.gitignore` on GitHub; they already exist.
3. From this folder:
   ```bash
   git init
   git add .
   git commit -m "Initial public release"
   git branch -M main
   git remote add origin https://github.com/aaronclashofclan1-lgtm/Outly.git
   git push -u origin main
   ```
4. Repository URLs already point to `aaronclashofclan1-lgtm/Outly`. If you fork
   or rename the repository, update `README.md`, `pyproject.toml`,
   `CITATION.cff`, `CONTRIBUTING.md`, `docs/INSTALL.md`, and
   `.github/ISSUE_TEMPLATE/config.yml`.

## 2. Repository settings

Suggested description:

> Local-first YouTube competitor intelligence, outlier detection, and content
> strategy for creators. Windows desktop app, SQLite, CSV/Markdown reports.

Suggested topics:

`youtube` `competitor-analysis` `content-strategy` `local-first` `sqlite`
`python` `customtkinter` `outlier-detection` `creator-tools` `windows`

Set the social preview to `assets/social-preview.png`. Enable **Private
vulnerability reporting** in Security settings. Enable Discussions only if you
can moderate them.

## 3. Branch protection

Protect `main`:

- require a pull request before merging;
- require the `test` CI job;
- require branches to be up to date;
- dismiss stale approvals;
- block force pushes and deletion;
- require conversation resolution.

## 4. First release

1. Confirm `scripts/verify_public_repo.py` passes.
2. Run the test suite and the build.
3. Confirm `release/Outly.exe` hash.
4. Tag `v1.0.0` and push the tag. The release workflow builds the executable
   and source archive.
5. Or run `scripts/build_windows.ps1` locally and attach the exe manually.
6. Publish the SHA-256 in the release notes.

## 5. What must never be committed

`.env`, databases, logs, reports, personal seeds/positioning, `Outly-app/`,
`Outly-partage/`, caches, and any API key. If a secret is ever committed,
revoke it immediately; deleting the commit is not enough.

## 6. Optional repository files

`CITATION.cff` is included. Add `FUNDING.yml` only if you have a funding page.
Add a `CODEOWNERS` file only after replacing the placeholder account with a real
GitHub username.
