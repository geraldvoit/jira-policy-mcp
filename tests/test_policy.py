"""Policy tests. Uses only invented projects/keys (ACME, DEMO) and no network."""

from __future__ import annotations

import pytest

from jira_policy_mcp.policy import Capability, Deployment, Policy, PolicyError
from jira_policy_mcp.registry import enabled_tool_names


def make_policy(**overrides) -> Policy:
    data = {
        "jira": {"base_url": "https://example.atlassian.net", "deployment": "cloud"},
        "allowed_projects": ["ACME"],
    }
    data.update(overrides)
    return Policy.from_dict(data)


# -- defaults -----------------------------------------------------------------


def test_default_policy_enables_only_read_by_key():
    policy = make_policy()
    assert policy.capabilities == frozenset({Capability.READ_BY_KEY})
    assert enabled_tool_names(policy) == {"jira_get_issue"}


def test_base_url_required():
    with pytest.raises(PolicyError):
        Policy.from_dict({"allowed_projects": ["ACME"]})


def test_deployment_parsed():
    policy = make_policy(jira={"base_url": "https://jira.example.com", "deployment": "datacenter"})
    assert policy.deployment is Deployment.DATACENTER


def test_invalid_deployment_rejected():
    with pytest.raises(PolicyError):
        make_policy(jira={"base_url": "https://x", "deployment": "nonsense"})


# -- key / project scoping ----------------------------------------------------


def test_allowed_key_normalized():
    policy = make_policy()
    assert policy.require_key_in_scope("acme-41".upper()) == "ACME-41"
    assert policy.require_key_in_scope(" ACME-41 ") == "ACME-41"


def test_key_outside_allowlist_rejected():
    policy = make_policy()
    with pytest.raises(PolicyError):
        policy.require_key_in_scope("DEMO-1")


def test_malformed_key_rejected():
    policy = make_policy()
    for bad in ["ACME", "ACME-", "-1", "acme1", "ACME 1", ""]:
        with pytest.raises(PolicyError):
            policy.require_key_in_scope(bad)


def test_project_keys_are_case_insensitive():
    policy = make_policy(allowed_projects=["acme", "Demo"])
    assert policy.require_key_in_scope("ACME-7") == "ACME-7"
    assert policy.require_key_in_scope("DEMO-7") == "DEMO-7"


# -- capability gating --------------------------------------------------------


def test_capability_gating_registers_only_enabled_tools():
    policy = make_policy(
        capabilities={"read_by_key": True, "search": True, "delete": False}
    )
    assert enabled_tool_names(policy) == {"jira_get_issue", "jira_search"}


def test_read_by_key_can_be_disabled_explicitly():
    policy = make_policy(capabilities={"read_by_key": False})
    assert enabled_tool_names(policy) == set()


def test_require_capability_raises_when_disabled():
    policy = make_policy()
    with pytest.raises(PolicyError):
        policy.require_capability(Capability.DELETE)


# -- field allowlist ----------------------------------------------------------


def test_fields_allowlist_blocks_unlisted_field():
    policy = make_policy(allowed_fields=["summary"])
    policy.require_fields_in_scope({"summary": "ok"})  # no raise
    with pytest.raises(PolicyError):
        policy.require_fields_in_scope({"summary": "ok", "assignee": "x"})


def test_fields_allowlist_empty_blocks_all_writes():
    policy = make_policy()  # no allowed_fields
    with pytest.raises(PolicyError):
        policy.require_fields_in_scope({"summary": "ok"})


def test_allow_all_fields_permits_any_field():
    policy = make_policy(allow_all_fields=True)
    # Any field, including arbitrary custom fields, is accepted.
    policy.require_fields_in_scope({"summary": "ok", "customfield_10010": ["x"]})


def test_create_capability_registers_create_and_metadata_tools():
    policy = make_policy(capabilities={"create": True})
    assert enabled_tool_names(policy) == {
        "jira_get_issue",
        "jira_create_issue",
        "jira_get_create_fields",
    }


# -- JQL guard ----------------------------------------------------------------


def test_jql_guard_always_injects_project_scope():
    policy = make_policy(allowed_projects=["ACME"])
    scoped = policy.build_scoped_jql("status = Open")
    assert scoped == "(status = Open) AND project IN (ACME)"


def test_jql_guard_constrains_attempt_to_escape():
    policy = make_policy(allowed_projects=["ACME"])
    # A query naming another project still gets AND-ed with the allowlist,
    # so it can return nothing outside ACME.
    scoped = policy.build_scoped_jql("project = SECRET")
    assert scoped.endswith("AND project IN (ACME)")
    assert "project = SECRET" in scoped


def test_jql_guard_preserves_order_by():
    policy = make_policy(allowed_projects=["ACME"])
    scoped = policy.build_scoped_jql("status = Open ORDER BY created DESC")
    assert scoped == "(status = Open) AND project IN (ACME) ORDER BY created DESC"


def test_jql_guard_empty_query():
    policy = make_policy(allowed_projects=["ACME", "DEMO"])
    scoped = policy.build_scoped_jql("")
    assert scoped == "project IN (ACME, DEMO) ORDER BY updated DESC"


def test_jql_guard_multiple_projects_sorted():
    policy = make_policy(allowed_projects=["DEMO", "ACME"])
    scoped = policy.build_scoped_jql("text ~ foo")
    assert "project IN (ACME, DEMO)" in scoped
