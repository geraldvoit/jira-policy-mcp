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

    reported_by_me = True
    own_keys: set[str] | None = None  # when set, only these count as the user's

    def is_reported_by_me(self, key):
        self.calls.append(("is_reported_by_me", key))
        if self.own_keys is not None:
            return key in self.own_keys
        return self.reported_by_me

    def update_issue(self, key, fields):
        self.calls.append(("update_issue", key, fields))

    def transition_issue(self, key, transition):
        self.calls.append(("transition_issue", key, transition))

    def link_issues(self, key, relation, other_key):
        self.calls.append(("link_issues", key, relation, other_key))
        return "Blocks"

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


def test_create_uses_defaults_of_target_project():
    policy = make_policy(
        allowed_projects=["ACME", "DEMO"],
        capabilities={"create": True},
        allowed_fields=["summary"],
        project_create_defaults={
            "ACME": {"customfield_10068": {"id": "1"}},
            "DEMO": {"customfield_10068": {"id": "2"}},
        },
    )
    fake = FakeClient()
    tools.create_issue(policy, lambda: fake, "demo", "Task", {"summary": "x"})
    _, project, _, fields = fake.calls[-1]
    assert project == "DEMO"
    assert fields["customfield_10068"] == {"id": "2"}


def _write_policy(**overrides):
    return make_policy(
        capabilities={"edit": True, "transition": True},
        allowed_fields=["summary"],
        **overrides,
    )


def test_write_scope_own_refuses_issue_created_by_someone_else():
    fake = FakeClient()
    fake.reported_by_me = False
    policy = _write_policy(write_scope="own")
    with pytest.raises(PolicyError):
        tools.update_issue(policy, lambda: fake, "ACME-1", {"summary": "x"})
    with pytest.raises(PolicyError):
        tools.transition_issue(policy, lambda: fake, "ACME-1", "Done")
    assert [c[0] for c in fake.calls] == ["is_reported_by_me", "is_reported_by_me"]


def test_write_scope_own_allows_own_issue():
    fake = FakeClient()
    policy = _write_policy(write_scope="own")
    tools.update_issue(policy, lambda: fake, "ACME-1", {"summary": "x"})
    assert fake.calls == [
        ("is_reported_by_me", "ACME-1"),
        ("update_issue", "ACME-1", {"summary": "x"}),
    ]


def test_write_scope_any_skips_ownership_check():
    fake = FakeClient()
    tools.transition_issue(_write_policy(), lambda: fake, "ACME-1", "Done")
    assert fake.calls == [("transition_issue", "ACME-1", "Done")]


def test_write_scope_own_logs_denial(tmp_path):
    fake = FakeClient()
    fake.reported_by_me = False
    with pytest.raises(PolicyError):
        tools.update_issue(_write_policy(write_scope="own"), lambda: fake, "ACME-1", {"summary": "x"})
    log = (tmp_path / "jira-policy-mcp" / "audit.log").read_text()
    assert '"decision": "deny"' in log and "not created by you" in log


def _link_policy(**overrides):
    return make_policy(allowed_projects=["ACME", "DEMO"], capabilities={"link": True}, **overrides)


def test_link_requires_capability():
    with pytest.raises(PolicyError):
        tools.link_issues(make_policy(), lambda: FakeClient(), "ACME-2", "is blocked by", "ACME-1")


def test_link_refuses_other_key_out_of_scope_without_a_call():
    factory_called = {"n": 0}

    def factory():
        factory_called["n"] += 1
        return FakeClient()

    with pytest.raises(PolicyError):
        tools.link_issues(_link_policy(), factory, "ACME-2", "is blocked by", "SECRET-1")
    assert factory_called["n"] == 0


def test_link_passes_normalized_keys_and_relation():
    fake = FakeClient()
    result = tools.link_issues(_link_policy(), lambda: fake, " ACME-2 ", "is blocked by", "DEMO-1")
    assert fake.calls == [("link_issues", "ACME-2", "is blocked by", "DEMO-1")]
    assert result == {
        "key": "ACME-2",
        "other_key": "DEMO-1",
        "relation": "is blocked by",
        "type": "Blocks",
        "linked": True,
    }


@pytest.mark.parametrize("own", [{"ACME-2"}, {"ACME-1"}])
def test_link_under_write_scope_own_needs_only_one_own_issue(own):
    fake = FakeClient()
    fake.own_keys = own
    tools.link_issues(_link_policy(write_scope="own"), lambda: fake, "ACME-2", "is blocked by", "ACME-1")
    assert fake.calls[-1] == ("link_issues", "ACME-2", "is blocked by", "ACME-1")


def test_link_under_write_scope_own_refuses_when_neither_is_own(tmp_path):
    fake = FakeClient()
    fake.own_keys = set()
    with pytest.raises(PolicyError, match="neither ACME-2 nor ACME-1 was created by you"):
        tools.link_issues(_link_policy(write_scope="own"), lambda: fake, "ACME-2", "blocks", "ACME-1")
    assert all(call[0] == "is_reported_by_me" for call in fake.calls)
    log = (tmp_path / "jira-policy-mcp" / "audit.log").read_text()
    assert '"tool": "jira_link_issues"' in log and '"decision": "deny"' in log
