"""JiraClient tests against a mocked transport. No network."""

from __future__ import annotations

import json

import httpx
import pytest

from jira_policy_mcp.jira_client import JiraClient, JiraError
from jira_policy_mcp.policy import Deployment


def make_client(reporter: dict | None, me: dict, deployment=Deployment.CLOUD):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/myself"):
            return httpx.Response(200, json=me)
        return httpx.Response(200, json={"fields": {"reporter": reporter}})

    http = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))
    return JiraClient(deployment=deployment, _client=http), seen


@pytest.mark.parametrize(
    ("reporter", "me", "expected"),
    [
        ({"accountId": "a1"}, {"accountId": "a1"}, True),
        ({"accountId": "b2"}, {"accountId": "a1"}, False),
        (None, {"accountId": "a1"}, False),
        ({}, {}, False),
    ],
)
def test_is_reported_by_me_compares_account_ids(reporter, me, expected):
    client, _ = make_client(reporter, me)
    assert client.is_reported_by_me("ACME-1") is expected


def test_is_reported_by_me_never_calls_search():
    client, seen = make_client({"accountId": "a1"}, {"accountId": "a1"})
    client.is_reported_by_me("ACME-1")
    assert seen == [("GET", "/rest/api/3/issue/ACME-1"), ("GET", "/rest/api/3/myself")]


def test_is_reported_by_me_uses_user_key_on_datacenter():
    client, _ = make_client({"key": "jdoe"}, {"key": "jdoe"}, Deployment.DATACENTER)
    assert client.is_reported_by_me("ACME-1") is True


LINK_TYPES = {
    "issueLinkTypes": [
        {"id": "1", "name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
        {"id": "2", "name": "Relates", "inward": "relates to", "outward": "relates to"},
    ]
}


def make_link_client():
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/issueLinkType"):
            return httpx.Response(200, json=LINK_TYPES)
        if request.method == "POST" and request.url.path.endswith("/issueLink"):
            posted.append(json.loads(request.content))
            return httpx.Response(201)
        return httpx.Response(404, json={})

    http = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))
    return JiraClient(deployment=Deployment.CLOUD, _client=http), posted


# Per Atlassian's KB example, outwardIssue=X, inwardIssue=Y means "X blocks Y".
@pytest.mark.parametrize(
    ("relation", "blocker", "blocked"),
    [
        ("blocks", "ACME-2", "ACME-1"),
        ("Blocks", "ACME-2", "ACME-1"),  # the type name reads as outward
        ("Is Blocked By", "ACME-1", "ACME-2"),
    ],
)
def test_link_issues_maps_relation_to_link_direction(relation, blocker, blocked):
    client, posted = make_link_client()
    assert client.link_issues("ACME-2", relation, "ACME-1") == "Blocks"
    assert posted == [
        {
            "type": {"name": "Blocks"},
            "outwardIssue": {"key": blocker},
            "inwardIssue": {"key": blocked},
        }
    ]


def test_link_issues_rejects_unknown_relation_before_posting():
    client, posted = make_link_client()
    with pytest.raises(JiraError, match="blocks / is blocked by"):
        client.link_issues("ACME-2", "depends on", "ACME-1")
    assert posted == []
