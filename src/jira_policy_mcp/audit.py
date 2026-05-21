"""Append-only audit log of every tool invocation and its policy decision.

Logging never raises into the request path: if the log cannot be written, the
request still proceeds. The log is machine-local (under the user's cache dir)
and is intentionally not synced anywhere.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX_VALUE_LEN = 500


def log_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    directory = Path(base) / "jira-policy-mcp"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "audit.log"


def _truncate(value: Any) -> Any:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) > _MAX_VALUE_LEN:
        return text[:_MAX_VALUE_LEN] + "…"
    return value


def record(tool: str, args: dict, decision: str, detail: str = "") -> None:
    """Append one entry. ``decision`` is "allow", "deny" or "error"."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "args": {k: _truncate(v) for k, v in (args or {}).items()},
        "decision": decision,
        "detail": detail,
    }
    try:
        with log_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
