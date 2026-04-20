"""Normalize remote .env line endings safely (CRLF/CR -> LF)."""
from __future__ import annotations

import pathlib


def main() -> None:
    env_path = pathlib.Path(".env")
    new_path = pathlib.Path(".env.new")
    if new_path.exists():
        raw = new_path.read_bytes()
        raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        env_path.write_bytes(raw)
        new_path.unlink()
        print(f"Replaced .env from .env.new ({len(raw)} bytes)")
    else:
        raw = env_path.read_bytes()
        raw2 = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if raw == raw2:
            print(".env already LF")
        else:
            env_path.write_bytes(raw2)
            print(f"Normalized .env ({len(raw2)} bytes)")


if __name__ == "__main__":
    main()
