"""Thin HTTP wrapper over the Jira REST API.

Abstracts the two deployment flavours:
  - Cloud:            REST API v3, Basic auth (email + API token), ADF bodies.
  - Data Center/Server: REST API v2, Bearer PAT, plain-text bodies.

This layer does no policy enforcement — callers must validate against the
policy first. Credentials are read from the environment and never logged.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import httpx

from .policy import Deployment, Policy


class JiraError(Exception):
    """Raised when the Jira API returns an error or auth is misconfigured."""


def _describe_field(field: dict) -> dict:
    """Normalize a createmeta field entry to {key, name, required, type, allowed_values}.

    ``allowed_values`` (id + value/name) is included only for fields that define
    a fixed option set, so the caller can discover e.g. which option "iOS" is.
    """
    schema = field.get("schema") or {}
    allowed = [
        {"id": v.get("id"), "value": v.get("value") or v.get("name")}
        for v in (field.get("allowedValues") or [])
    ]
    described = {
        "key": field.get("fieldId"),
        "name": field.get("name"),
        "required": field.get("required", False),
        "type": schema.get("custom") or schema.get("type"),
    }
    if allowed:
        described["allowed_values"] = allowed
    return described


def _adf(text: str) -> dict:
    """Wrap plain text in a minimal Atlassian Document Format doc (Cloud v3)."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        ],
    }


@dataclass
class JiraClient:
    deployment: Deployment
    _client: httpx.Client

    @classmethod
    def from_policy(cls, policy: Policy) -> "JiraClient":
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        if policy.deployment is Deployment.CLOUD:
            email = os.environ.get("JIRA_EMAIL")
            token = os.environ.get("JIRA_API_TOKEN")
            if not email or not token:
                raise JiraError(
                    "Jira Cloud requires JIRA_EMAIL and JIRA_API_TOKEN in the environment"
                )
            raw = f"{email}:{token}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        else:
            pat = os.environ.get("JIRA_PAT") or os.environ.get("JIRA_API_TOKEN")
            if not pat:
                raise JiraError(
                    "Jira Data Center/Server requires JIRA_PAT in the environment"
                )
            headers["Authorization"] = f"Bearer {pat}"

        client = httpx.Client(base_url=policy.base_url, headers=headers, timeout=30.0)
        return cls(deployment=policy.deployment, _client=client)

    # -- internals ------------------------------------------------------------

    @property
    def _api(self) -> str:
        return "/rest/api/3" if self.deployment is Deployment.CLOUD else "/rest/api/2"

    @property
    def _is_cloud(self) -> bool:
        return self.deployment is Deployment.CLOUD

    def _body_field(self, text: str):
        return _adf(text) if self._is_cloud else text

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._client.request(method, f"{self._api}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise JiraError(f"request to Jira failed: {exc}") from exc
        if response.status_code >= 400:
            raise JiraError(
                f"Jira returned {response.status_code} for {method} {path}: "
                f"{response.text[:300]}"
            )
        return response

    # -- read -----------------------------------------------------------------

    def get_issue(self, key: str) -> dict:
        params = {"fields": "summary,status,issuetype,assignee,description,labels,updated"}
        return self._request("GET", f"/issue/{key}", params=params).json()

    def search(self, jql: str, max_results: int) -> dict:
        payload = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status", "issuetype", "updated"],
        }
        return self._request("POST", "/search", json=payload).json()

    def get_comments(self, key: str) -> dict:
        return self._request("GET", f"/issue/{key}/comment").json()

    def get_transitions(self, key: str) -> dict:
        return self._request("GET", f"/issue/{key}/transitions").json()

    def get_create_fields(self, project: str, issue_type: str) -> dict:
        """Return the fields available when creating an issue of this type.

        Each entry is {"key", "name", "required"}. Use it to discover what may
        (or must) be set before calling jira_create_issue.
        """
        if self._is_cloud:
            return self._create_fields_cloud(project, issue_type)
        return self._create_fields_datacenter(project, issue_type)

    def _create_fields_cloud(self, project: str, issue_type: str) -> dict:
        types_resp = self._request(
            "GET",
            f"/issue/createmeta/{project}/issuetypes",
            params={"maxResults": 200},
        ).json()
        # The endpoint returns the array under "issueTypes"; older/edge responses
        # use "values". Accept either so we don't silently see "none".
        issue_types = types_resp.get("issueTypes") or types_resp.get("values") or []
        match = next(
            (t for t in issue_types if t.get("name", "").lower() == issue_type.lower()),
            None,
        )
        if match is None:
            names = ", ".join(t.get("name", "?") for t in issue_types) or "none"
            raise JiraError(
                f"issue type {issue_type!r} not available for {project} (available: {names})"
            )
        meta = self._request(
            "GET",
            f"/issue/createmeta/{project}/issuetypes/{match['id']}",
            params={"maxResults": 200},
        ).json()
        field_list = meta.get("fields") or meta.get("values") or []
        fields = [_describe_field(field) for field in field_list]
        return {"project": project, "issue_type": match.get("name"), "fields": fields}

    def _create_fields_datacenter(self, project: str, issue_type: str) -> dict:
        params = {
            "projectKeys": project,
            "issuetypeNames": issue_type,
            "expand": "projects.issuetypes.fields",
        }
        data = self._request("GET", "/issue/createmeta", params=params).json()
        projects = data.get("projects", [])
        issuetypes = projects[0].get("issuetypes", []) if projects else []
        if not issuetypes:
            raise JiraError(
                f"no create metadata for {project}/{issue_type} "
                "(check the project key and issue type name)"
            )
        field_map = issuetypes[0].get("fields", {})
        fields = [
            _describe_field({"fieldId": key, **meta}) for key, meta in field_map.items()
        ]
        return {"project": project, "issue_type": issue_type, "fields": fields}

    # -- write ----------------------------------------------------------------

    def add_comment(self, key: str, body: str) -> dict:
        return self._request(
            "POST", f"/issue/{key}/comment", json={"body": self._body_field(body)}
        ).json()

    def create_issue(self, project: str, issue_type: str, fields: dict) -> dict:
        payload_fields = {
            "project": {"key": project},
            "issuetype": {"name": issue_type},
        }
        payload_fields.update(self._normalize_fields(fields))
        return self._request("POST", "/issue", json={"fields": payload_fields}).json()

    def update_issue(self, key: str, fields: dict) -> None:
        self._request(
            "PUT", f"/issue/{key}", json={"fields": self._normalize_fields(fields)}
        )

    def transition_issue(self, key: str, transition: str) -> None:
        transition_id = self._resolve_transition(key, transition)
        self._request(
            "POST", f"/issue/{key}/transitions", json={"transition": {"id": transition_id}}
        )

    def delete_issue(self, key: str) -> None:
        self._request("DELETE", f"/issue/{key}")

    # -- helpers --------------------------------------------------------------

    def _normalize_fields(self, fields: dict) -> dict:
        """On Cloud, rich-text fields must be ADF rather than plain strings."""
        if not self._is_cloud:
            return dict(fields)
        normalized = {}
        for name, value in fields.items():
            normalized[name] = (
                self._body_field(value)
                if name == "description" and isinstance(value, str)
                else value
            )
        return normalized

    def _resolve_transition(self, key: str, transition: str) -> str:
        """Accept either a transition id or a (case-insensitive) name."""
        if transition.isdigit():
            return transition
        available = self.get_transitions(key).get("transitions", [])
        for item in available:
            if item.get("name", "").lower() == transition.lower():
                return item["id"]
        names = ", ".join(t.get("name", "?") for t in available) or "none"
        raise JiraError(
            f"transition {transition!r} not available for {key} (available: {names})"
        )

    def close(self) -> None:
        self._client.close()
