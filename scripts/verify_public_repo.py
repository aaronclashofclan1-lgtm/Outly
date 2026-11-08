#!/usr/bin/env python3
"""Fail when a public Outly tree contains private files or obvious secrets."""

from __future__ import annotations

import re
import sys
from pathlib import Path

FORBIDDEN_NAMES = {".env", ".coverage"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".log"}
SKIP_DIRS = {".git", "__pycache__", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?:YOUTUBE_API_KEY|LLM_API_KEY|LLM_FALLBACK_API_KEY|EXA_API_KEY|SMTP_PASSWORD|YOUTUBE_OAUTH_CLIENT_SECRET|YOUTUBE_REFRESH_TOKEN)=(?!replace_|your_|\.\.\.|$)\S{12,}"),
]


def main(root_arg: str = ".") -> int:
    root = Path(root_arg).resolve()
    errors: list[str] = []
    files = 0
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        files += 1
        rel = path.relative_to(root)
        if path.name in FORBIDDEN_NAMES:
            errors.append(f"forbidden file: {rel}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden data/log file: {rel}")
        if path.name.endswith(".zip") and path.name != "Outly-source.zip":
            errors.append(f"unexpected archive: {rel}")
        if path.stat().st_size > 60 * 1024 * 1024:
            errors.append(f"file is unexpectedly large: {rel}")
        if path.suffix.lower() in {".exe", ".png", ".ico", ".pdf", ".zip", ".jpg", ".jpeg"}:
            continue
        # Test fixtures deliberately contain fake keys and fake .env lines.
        # Name/suffix checks above still apply, so a real database or log in
        # tests/ is rejected.
        if rel.parts and rel.parts[0] == "tests":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in PATTERNS:
            if pattern.search(text):
                errors.append(f"possible secret in: {rel}")
                break
    if not (root / "README.md").is_file():
        errors.append("README.md is missing")
    if not (root / "LICENSE").is_file():
        errors.append("LICENSE is missing")
    if not (root / "docs" / "USER_GUIDE.pdf").is_file():
        errors.append("docs/USER_GUIDE.pdf is missing")
    for rel in (
        "outly/modeles/config.yaml",
        "outly/modeles/env.modele",
        "outly/modeles/keywords.txt",
        "outly/modeles/positioning.md",
        "outly/modeles/seeds.txt",
    ):
        if not (root / rel).is_file():
            errors.append(f"required first-run template is missing: {rel}")
    print(f"scanned {files} files under {root}")
    if errors:
        print("FAILED")
        for error in errors:
            print(f" - {error}")
        return 1
    print("OK: no private files or obvious secret patterns found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
