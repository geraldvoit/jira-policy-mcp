"""Tool implementations.

Each function enforces the policy *before* doing anything else, records the
decision to the audit log, and only then reaches for the Jira client (obtained
lazily via ``get_client`` so a denied request never needs credentials).

These are plain functions; ``server.py`` wraps the enabled ones as MCP tools.
"""

from __future__ import annotations

from typing import Callable

from . import audit
from .jira_client import JiraClient
from .policy import Capability, Policy, PolicyError

ClientFactory = Callable[[], JiraClient]


def _guard(tool: str, args: dict, check: Callable[[], None]) -> None:
    """Run a policy check, recording a deny entry and re-raising on failure."""
    try:
        check()
    except PolicyError as exc:
        audit.record(tool, args, "deny", str(exc))
        raise


def get_issue(policy: Policy, get_client: ClientFactory, key: str) -> dict:
    args = {"key": key}
    normalized = {"value": ""}

    def check():
        policy.require_capability(Capability.READ_BY_KEY)
        normalized["value"] = policy.require_key_in_scope(key)

    _guard("jira_get_issue", args, check)
    issue = get_client().get_issue(normalized["value"])
    audit.record("jira_get_issue", {"key": normalized["value"]}, "allow")
    return issue


def search(policy: Policy, get_client: ClientFactory, jql: str = "", max_results: int = 0) -> dict:
    args = {"jql": jql, "max_results": max_results}
    scoped = {"value": ""}

    def check():
        policy.require_capability(Capability.SEARCH)
        scoped["value"] = policy.build_scoped_jql(jql)

    _guard("jira_search", args, check)
    limit = min(max_results or policy.max_search_results, policy.max_search_results)
    result = get_client().search(scoped["value"], limit)
    audit.record("jira_search", {"jql": scoped["value"], "max_results": limit}, "allow")
    return result


def get_comments(policy: Policy, get_client: ClientFactory, key: str) -> dict:
    args = {"key": key}
    normalized = {"value": ""}

    def check():
        policy.require_capability(Capability.READ_COMMENTS)
        normalized["value"] = policy.require_key_in_scope(key)

    _guard("jira_get_comments", args, check)
    result = get_client().get_comments(normalized["value"])
    audit.record("jira_get_comments", {"key": normalized["value"]}, "allow")
    return result


def add_comment(policy: Policy, get_client: ClientFactory, key: str, body: str) -> dict:
    args = {"key": key, "body": body}
    normalized = {"value": ""}

    def check():
        policy.require_capability(Capability.ADD_COMMENT)
        normalized["value"] = policy.require_key_in_scope(key)

    _guard("jira_add_comment", args, check)
    result = get_client().add_comment(normalized["value"], body)
    audit.record("jira_add_comment", {"key": normalized["value"]}, "allow")
    return result


def get_create_fields(
    policy: Policy, get_client: ClientFactory, project: str, issue_type: str
) -> dict:
    args = {"project": project, "issue_type": issue_type}

    def check():
        policy.require_capability(Capability.CREATE)
        policy.require_project_in_scope(project)

    _guard("jira_get_create_fields", args, check)
    result = get_client().get_create_fields(project.upper(), issue_type)
    audit.record("jira_get_create_fields", args, "allow")
    return result


def create_issue(
    policy: Policy, get_client: ClientFactory, project: str, issue_type: str, fields: dict
) -> dict:
    fields = fields or {}
    args = {"project": project, "issue_type": issue_type, "fields": fields}

    def check():
        policy.require_capability(Capability.CREATE)
        policy.require_project_in_scope(project)
        policy.require_fields_in_scope(fields)

    _guard("jira_create_issue", args, check)
    result = get_client().create_issue(project.upper(), issue_type, fields)
    audit.record("jira_create_issue", {"project": project, "issue_type": issue_type}, "allow")
    return result


def update_issue(policy: Policy, get_client: ClientFactory, key: str, fields: dict) -> dict:
    fields = fields or {}
    args = {"key": key, "fields": fields}
    normalized = {"value": ""}

    def check():
        policy.require_capability(Capability.EDIT)
        normalized["value"] = policy.require_key_in_scope(key)
        policy.require_fields_in_scope(fields)

    _guard("jira_update_issue", args, check)
    get_client().update_issue(normalized["value"], fields)
    audit.record("jira_update_issue", {"key": normalized["value"]}, "allow")
    return {"key": normalized["value"], "updated": True}


def transition_issue(policy: Policy, get_client: ClientFactory, key: str, transition: str) -> dict:
    args = {"key": key, "transition": transition}
    normalized = {"value": ""}

    def check():
        policy.require_capability(Capability.TRANSITION)
        normalized["value"] = policy.require_key_in_scope(key)

    _guard("jira_transition_issue", args, check)
    get_client().transition_issue(normalized["value"], transition)
    audit.record("jira_transition_issue", {"key": normalized["value"], "transition": transition}, "allow")
    return {"key": normalized["value"], "transition": transition, "applied": True}


def delete_issue(policy: Policy, get_client: ClientFactory, key: str) -> dict:
    args = {"key": key}
    normalized = {"value": ""}

    def check():
        policy.require_capability(Capability.DELETE)
        normalized["value"] = policy.require_key_in_scope(key)

    _guard("jira_delete_issue", args, check)
    get_client().delete_issue(normalized["value"])
    audit.record("jira_delete_issue", {"key": normalized["value"]}, "allow")
    return {"key": normalized["value"], "deleted": True}
