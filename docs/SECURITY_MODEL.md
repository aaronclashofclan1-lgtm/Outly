# Security model

## Trust boundary

Outly is a single-user Windows desktop application. It assumes the Windows
account and the application folder are trusted. It does not defend against a
local attacker who can read the folder, replace the executable, or attach a
debugger.

The network is untrusted. Every API response is parsed as untrusted data.
Provider errors, malformed JSON, unexpected types, timeouts, and rate limits
must produce a controlled message or a clean stop, never a secondary crash.

## Assets

- API keys, OAuth client secret, refresh token, and Gmail app password in
  `.env`.
- Local SQLite database and reports, which may contain private research.
- The integrity of generated files: config, reports, whiteboard, plan, and the
  scheduled `.bat` file.
- The OAuth loopback callback and state value.

## Controls

- `.env` is excluded from Git, release archives, and reports.
- Secrets are masked in the GUI and never logged.
- First-run configuration copy is atomic and never overwrites a file.
- Reports, whiteboard, and scheduled scripts use temporary files plus atomic
  replacement.
- Database writes use transactions; the app retries transient I/O errors and
  reports an unreadable database instead of showing an empty one.
- OAuth uses a local `127.0.0.1` callback and a random state value.
- HTTP clients have timeouts, bounded retries, and typed errors.
- Cache data has a 30-day ceiling.
- CI runs the test suite on Windows and audits pinned dependencies.

## Known limits

- The executable is not code-signed; verify the published hash.
- The interface has limited automated coverage.
- Full static analysis is not a strict gate yet.
- A compromised local account can read `.env` and the database.
- Third-party services can change their APIs, quotas, or retention terms.

## Reporting

See [SECURITY.md](../SECURITY.md). Never include live credentials or personal
data in a public issue.
