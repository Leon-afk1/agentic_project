"""Where secrets come from: the process environment, optionally fed by `.env`.

Rules enforced here:
- a key is read from the environment only, never from a command-line argument
  (arguments end up in shell history and process lists);
- `.env` is git-ignored and never overrides a variable that is already set;
- no function in this module returns, logs or formats a key into a message.
"""

from __future__ import annotations

import os
from pathlib import Path

API_KEY_VARIABLE = "GEMINI_API_KEY"


class MissingKeyError(RuntimeError):
    pass


def load_env(path: Path = Path(".env")) -> list[str]:
    """Load KEY=VALUE lines into the environment. Returns the names that were set."""
    if not path.exists():
        return []
    loaded: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and value and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def require_key(variable: str = API_KEY_VARIABLE) -> str:
    key = os.environ.get(variable, "").strip()
    if not key:
        raise MissingKeyError(
            f"{variable} is not set. Copy .env.example to .env and put your key there."
        )
    return key


def describe_key(variable: str = API_KEY_VARIABLE) -> str:
    """A safe description for status messages: presence and length, nothing else."""
    key = os.environ.get(variable, "").strip()
    return f"{variable}: set ({len(key)} characters)" if key else f"{variable}: not set"
