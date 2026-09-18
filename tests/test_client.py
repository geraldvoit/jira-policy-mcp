"""JiraClient tests against a mocked transport. No network."""

from __future__ import annotations

import httpx
import pytest

from jira_policy_mcp.jira_client import JiraClient
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
