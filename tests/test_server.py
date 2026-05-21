"""Server-level test: only enabled capabilities register MCP tools."""

from __future__ import annotations

import asyncio

from jira_policy_mcp.policy import Policy
from jira_policy_mcp.server import build_server


def _tool_names(policy: Policy) -> set[str]:
    server = build_server(policy)
    tools = asyncio.run(server.list_tools())
    return {tool.name for tool in tools}


def make_policy(**overrides) -> Policy:
    data = {
        "jira": {"base_url": "https://example.atlassian.net", "deployment": "cloud"},
        "allowed_projects": ["ACME"],
    }
    data.update(overrides)
    return Policy.from_dict(data)


def test_default_policy_registers_only_get_issue():
    assert _tool_names(make_policy()) == {"jira_get_issue"}


def test_enabling_capabilities_registers_their_tools():
    policy = make_policy(
        capabilities={"read_by_key": True, "search": True, "add_comment": True}
    )
    assert _tool_names(policy) == {"jira_get_issue", "jira_search", "jira_add_comment"}


def test_disabled_delete_is_not_registered():
    policy = make_policy(capabilities={"read_by_key": True, "delete": False})
    assert "jira_delete_issue" not in _tool_names(policy)
