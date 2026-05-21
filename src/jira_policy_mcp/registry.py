"""Mapping from policy capabilities to the MCP tools they unlock.

Kept dependency-free (no mcp/httpx imports) so both the server and the tests
can reason about which tools a policy exposes without standing up a client.
"""

from __future__ import annotations

from .policy import Capability, Policy

# One capability may unlock one or more tools. The server registers exactly the
# tools returned by ``enabled_tool_names`` and nothing else.
CAPABILITY_TOOL_NAMES: dict[Capability, tuple[str, ...]] = {
    Capability.READ_BY_KEY: ("jira_get_issue",),
    Capability.SEARCH: ("jira_search",),
    Capability.READ_COMMENTS: ("jira_get_comments",),
    Capability.ADD_COMMENT: ("jira_add_comment",),
    Capability.CREATE: ("jira_create_issue", "jira_get_create_fields"),
    Capability.EDIT: ("jira_update_issue",),
    Capability.TRANSITION: ("jira_transition_issue",),
    Capability.DELETE: ("jira_delete_issue",),
}


def enabled_tool_names(policy: Policy) -> set[str]:
    names: set[str] = set()
    for capability, tools in CAPABILITY_TOOL_NAMES.items():
        if policy.has(capability):
            names.update(tools)
    return names
