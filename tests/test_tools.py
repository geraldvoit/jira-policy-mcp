"""Tool-level tests with a fake client: denied requests make no network call."""

from __future__ import annotations

import pytest

from jira_policy_mcp import tools
from jira_policy_mcp.policy import Policy, PolicyError


@pytest.fixture(autouse=True)
def _isolate_audit(tmp_path, monkeypatch):
    # Keep the audit log out of the real ~/.cache during tests.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_issue(self, key):
        self.calls.append(("get_issue", key))
        return {"key": key, "fields": {"summary": "hi"}}

    def create_issue(self, project, issue_type, fields):
        self.calls.append(("create_issue", project, issue_type, fields))
        return {"key": f"{project}-1"}


def make_policy(**overrides) -> Policy:
    data = {
        "jira": {"base_url": "https://example.atlassian.net", "deployment": "cloud"},
        "allowed_projects": ["ACME"],
    }
    data.update(overrides)
    return Policy.from_dict(data)


def test_get_issue_allowed_calls_client():
    policy = make_policy()
    fake = FakeClient()
    result = tools.get_issue(policy, lambda: fake, "ACME-41")
    assert result["key"] == "ACME-41"
    assert fake.calls == [("get_issue", "ACME-41")]


def test_get_issue_out_of_scope_makes_no_call():
    policy = make_policy()
    factory_called = {"n": 0}

    def factory():
        factory_called["n"] += 1
        return FakeClient()

    with pytest.raises(PolicyError):
        tools.get_issue(policy, factory, "DEMO-1")
    assert factory_called["n"] == 0  # client never built on a denied request


def test_get_comments_requires_capability():
    policy = make_policy()  # read_comments off by default
    with pytest.raises(PolicyError):
        tools.get_comments(policy, lambda: FakeClient(), "ACME-41")


def test_create_defaults_override_agent_fields():
    policy = make_policy(
        capabilities={"create": True},
        allow_all_fields=True,
        create_defaults={"customfield_10068": {"value": "iOS"}},
    )
    fake = FakeClient()
    tools.create_issue(
        policy,
        lambda: fake,
        "acme",
        "Task",
        {"summary": "x", "customfield_10068": {"value": "android"}},
    )
    _, project, issue_type, fields = fake.calls[-1]
    assert project == "ACME"  # normalized
    assert fields["summary"] == "x"
    assert fields["customfield_10068"] == {"value": "iOS"}  # default wins
