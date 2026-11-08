# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | Yes |
| older | No; rebuild from the current source |

## Reporting a vulnerability

**Do not open a public issue for a vulnerability.**

Use GitHub's **private vulnerability reporting** for this repository
(Security -> Report a vulnerability). If that is unavailable, contact the
maintainers through their GitHub profile and ask for a private channel.

Include:

- affected version and operating system;
- a minimal reproduction or proof of concept;
- impact and affected files/commands;
- whether any secret, local database, or personal data may have been exposed;
- a safe way to contact you.

Do not include live API keys, OAuth tokens, SMTP passwords, `.env` contents, or
personal databases. Rotate any credential that may have been exposed before
sending a report.

## Scope

In scope: secret leakage through the GUI, CLI, reports, logs, source archive, or
build; path traversal or unsafe file writes; SQL injection or database
corruption; OAuth callback/state handling; SSRF or credential leakage in
provider clients; dependency vulnerabilities with practical impact; denial of
service caused by malformed API or user input.

Out of scope: YouTube/Google, LLM, Exa, or Gmail account issues; provider quota
policy; SmartScreen warnings from the unsigned executable; findings that require
a compromised local machine or a stolen `.env`; hardening without demonstrated
impact.

## Security model

Outly is a local desktop application, not a multi-user web service. It assumes
the local Windows account is trusted. The `.env` file and the SQLite database
are sensitive: anyone who can read the folder can read the keys and data.

The app opens an OAuth loopback server on `127.0.0.1` during YouTube Studio
connection. It sends requests only to endpoints configured by the user. It has
no telemetry and no remote database.

## Release hygiene

- `.env`, databases, logs, reports, caches, and personal folders are excluded
  from Git.
- `scripts/verify_public_repo.py` checks the tree for forbidden files and
  obvious secret patterns.
- Never publish a build from a folder that contains a real `.env`.
- The executable is unsigned; publish its SHA-256 with every release.
