"""Policy model and enforcement.

The policy is the security heart of this server: it decides which projects the
agent may touch and which operations are even possible. Two enforcement layers
build on it:

1. Tool registration — the server only registers MCP tools whose capability is
   enabled here, so a disabled operation does not exist for the agent at all.
2. Runtime validation — every call re-checks the issue key format, the project
   allowlist and (for writes) the field allowlist before any network request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

# A Jira issue key is a project key (upper-case, starts with a letter) plus a
# numeric counter, e.g. ACME-123.
ISSUE_KEY_RE = re.compile(r"^([A-Z][A-Z0-9]+)-(\d+)$")

# Splits a JQL string at its trailing ORDER BY clause, if any.
_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)


class PolicyError(Exception):
    """Raised when a request is rejected by the configured policy."""


class Capability(str, Enum):
    READ_BY_KEY = "read_by_key"
    SEARCH = "search"
    READ_COMMENTS = "read_comments"
    ADD_COMMENT = "add_comment"
    CREATE = "create"
    EDIT = "edit"
    TRANSITION = "transition"
    DELETE = "delete"


class Deployment(str, Enum):
    CLOUD = "cloud"
    DATACENTER = "datacenter"


@dataclass(frozen=True)
class Policy:
    base_url: str
    deployment: Deployment
    allowed_projects: frozenset[str]
    capabilities: frozenset[Capability]
    allowed_fields: frozenset[str]
    allow_all_fields: bool
    create_defaults: dict
    max_search_results: int

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "Policy":
        if not isinstance(data, dict):
            raise PolicyError("policy file must contain a top-level mapping")

        jira = data.get("jira") or {}
        base_url = (jira.get("base_url") or "").strip()
        if not base_url:
            raise PolicyError("policy: jira.base_url is required")
        try:
            deployment = Deployment(jira.get("deployment", "cloud"))
        except ValueError:
            raise PolicyError(
                "policy: jira.deployment must be 'cloud' or 'datacenter'"
            ) from None

        allowed_projects = frozenset(
            str(p).strip().upper()
            for p in (data.get("allowed_projects") or [])
            if str(p).strip()
        )

        # Every capability is off unless explicitly enabled, with the single
        # exception of read_by_key, which defaults on so a fresh policy can at
        # least read one ticket by key.
        caps_cfg = data.get("capabilities") or {}
        enabled = {
            cap
            for cap in Capability
            if bool(caps_cfg.get(cap.value, cap is Capability.READ_BY_KEY))
        }

        allowed_fields = frozenset(
            str(f).strip() for f in (data.get("allowed_fields") or []) if str(f).strip()
        )
        allow_all_fields = bool(data.get("allow_all_fields", False))
        create_defaults = dict(data.get("create_defaults") or {})
        try:
            max_results = int(data.get("max_search_results", 25))
        except (TypeError, ValueError):
            raise PolicyError("policy: max_search_results must be an integer") from None

        return cls(
            base_url=base_url.rstrip("/"),
            deployment=deployment,
            allowed_projects=allowed_projects,
            capabilities=frozenset(enabled),
            allowed_fields=allowed_fields,
            allow_all_fields=allow_all_fields,
            create_defaults=create_defaults,
            max_search_results=max(1, max_results),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        path = Path(path).expanduser()
        if not path.is_file():
            raise PolicyError(f"policy file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.from_dict(data)

    # -- checks ---------------------------------------------------------------

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require_capability(self, capability: Capability) -> None:
        if capability not in self.capabilities:
            raise PolicyError(
                f"capability {capability.value!r} is disabled by policy"
            )

    def parse_key(self, key: str) -> tuple[str, str]:
        """Return (project, normalized_key) or raise on a malformed key."""
        match = ISSUE_KEY_RE.match((key or "").strip())
        if not match:
            raise PolicyError(
                f"invalid issue key {key!r} (expected a key like ACME-123)"
            )
        return match.group(1), match.group(0)

    def require_project_in_scope(self, project: str) -> None:
        if project.upper() not in self.allowed_projects:
            allowed = ", ".join(sorted(self.allowed_projects)) or "none"
            raise PolicyError(
                f"project {project!r} is not in the allowed list ({allowed})"
            )

    def require_key_in_scope(self, key: str) -> str:
        """Validate format and project allowlist; return the normalized key."""
        project, normalized = self.parse_key(key)
        self.require_project_in_scope(project)
        return normalized

    def require_fields_in_scope(self, fields: dict) -> None:
        if self.allow_all_fields:
            return
        if not self.allowed_fields:
            raise PolicyError(
                "no fields are write-allowed by policy "
                "(set allowed_fields, or allow_all_fields: true)"
            )
        disallowed = sorted(set(fields) - self.allowed_fields)
        if disallowed:
            raise PolicyError(
                "fields not write-allowed by policy: " + ", ".join(disallowed)
            )

    def build_scoped_jql(self, user_jql: str) -> str:
        """Wrap user JQL so results can never escape the allowed projects.

        The user's clause is AND-combined with ``project IN (<allowed>)``. Even
        a query like ``project = SECRET`` becomes ``(project = SECRET) AND
        project IN (ACME)`` and returns nothing — the allowlist always wins. A
        trailing ORDER BY is preserved at the end so the result stays valid JQL.
        """
        if not self.allowed_projects:
            raise PolicyError("search is not possible: no allowed_projects configured")

        guard = "project IN ({})".format(", ".join(sorted(self.allowed_projects)))
        jql = (user_jql or "").strip()
        if not jql:
            return f"{guard} ORDER BY updated DESC"

        match = _ORDER_BY_RE.search(jql)
        if match:
            where = jql[: match.start()].strip()
            order = jql[match.start():].strip()
        else:
            where, order = jql, ""

        scoped = f"({where}) AND {guard}" if where else guard
        return f"{scoped} {order}".strip()
