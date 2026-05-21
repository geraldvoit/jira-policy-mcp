"""MCP server entry point.

Loads the policy, registers *only* the tools whose capability is enabled, and
serves over stdio. Tools that are off in the policy are never registered, so
the agent cannot call (or even see) them.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import tools
from .jira_client import JiraClient
from .policy import Capability, Policy


class _LazyClient:
    """Builds the Jira client on first use, so policy denials need no creds."""

    def __init__(self, policy: Policy) -> None:
        self._policy = policy
        self._client: JiraClient | None = None

    def get(self) -> JiraClient:
        if self._client is None:
            self._client = JiraClient.from_policy(self._policy)
        return self._client


def build_server(policy: Policy) -> FastMCP:
    mcp = FastMCP("jira-policy-mcp")
    client = _LazyClient(policy)

    if policy.has(Capability.READ_BY_KEY):

        @mcp.tool()
        def jira_get_issue(key: str) -> dict:
            """Read a single Jira issue by its key (e.g. ACME-123)."""
            return tools.get_issue(policy, client.get, key)

    if policy.has(Capability.SEARCH):

        @mcp.tool()
        def jira_search(jql: str = "", max_results: int = 0) -> dict:
            """Search issues with JQL. Results are always constrained to the
            policy's allowed projects."""
            return tools.search(policy, client.get, jql, max_results)

    if policy.has(Capability.READ_COMMENTS):

        @mcp.tool()
        def jira_get_comments(key: str) -> dict:
            """Read the comments on a Jira issue."""
            return tools.get_comments(policy, client.get, key)

    if policy.has(Capability.ADD_COMMENT):

        @mcp.tool()
        def jira_add_comment(key: str, body: str) -> dict:
            """Add a comment to a Jira issue."""
            return tools.add_comment(policy, client.get, key, body)

    if policy.has(Capability.CREATE):

        @mcp.tool()
        def jira_get_create_fields(project: str, issue_type: str) -> dict:
            """List the fields available (and which are required) when creating
            an issue of the given type in an allowed project."""
            return tools.get_create_fields(policy, client.get, project, issue_type)

        @mcp.tool()
        def jira_create_issue(project: str, issue_type: str, fields: dict) -> dict:
            """Create a new issue in an allowed project. Fields are subject to
            the policy's field rules (allowed_fields / allow_all_fields)."""
            return tools.create_issue(policy, client.get, project, issue_type, fields)

    if policy.has(Capability.EDIT):

        @mcp.tool()
        def jira_update_issue(key: str, fields: dict) -> dict:
            """Update fields on an existing issue. Only policy-allowed fields
            may be set."""
            return tools.update_issue(policy, client.get, key, fields)

    if policy.has(Capability.TRANSITION):

        @mcp.tool()
        def jira_transition_issue(key: str, transition: str) -> dict:
            """Move an issue through a workflow transition (by id or name)."""
            return tools.transition_issue(policy, client.get, key, transition)

    if policy.has(Capability.DELETE):

        @mcp.tool()
        def jira_delete_issue(key: str) -> dict:
            """Delete an issue. Disabled unless explicitly enabled in policy."""
            return tools.delete_issue(policy, client.get, key)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jira-policy-mcp",
        description="A policy-enforcing MCP server in front of the Jira REST API.",
    )
    parser.add_argument(
        "--policy",
        required=True,
        type=Path,
        help="Path to the policy YAML file.",
    )
    args = parser.parse_args()

    policy = Policy.load(args.policy)
    server = build_server(policy)
    server.run()  # stdio transport by default


if __name__ == "__main__":
    main()
