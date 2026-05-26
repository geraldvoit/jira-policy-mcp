# jira-policy-mcp

A small, local **MCP server that gives an AI coding agent (e.g. Claude Code) scoped, least-privilege access to Jira.**

Jira has no equivalent of GitHub's fine-grained tokens: an API token inherits *all* of your permissions across *every* project (read, write, delete). Handing that token to an agent — or wiring the agent to the official Atlassian MCP server over OAuth — effectively grants it your entire Jira instance.

This server sits in front of the Jira REST API and enforces a policy you control:

- **Which projects** the agent may touch (allowlist).
- **Which operations** are even possible (read by key, search, comment, create, edit, transition, delete) — each off by default.

The Jira credential lives only in this server's process. The agent never sees it.

## How it enforces scope

1. **Tool registration** — only tools whose capability is enabled in your policy are registered with the agent. A disabled operation simply does not exist for it.
2. **Runtime validation** — every call re-checks the issue-key format, the project allowlist and (for writes) the field allowlist *before* any network request. `jira_search` always has `project IN (<allowed>)` AND-injected into the JQL, so a search can never return issues outside scope.

Every request — allowed or denied — is appended to an audit log at `~/.cache/jira-policy-mcp/audit.log`.

## Install

```bash
pipx install git+https://github.com/geraldvoit/jira-policy-mcp
# or, with uv:
uvx --from git+https://github.com/geraldvoit/jira-policy-mcp jira-policy-mcp --policy ./policy.yml
```

## Configure

Copy `policy.example.yml` to your own `policy.yml` (gitignored) and edit it. The defaults are intentionally minimal — read a single issue by key, nothing else:

```yaml
jira:
  base_url: https://your-domain.atlassian.net
  deployment: cloud            # cloud | datacenter
allowed_projects: [ACME]
capabilities:
  read_by_key: true            # everything else defaults false
```

### Credentials (never in the policy file)

The server reads credentials from the environment:

| Deployment | URL looks like | Env vars |
|---|---|---|
| **Cloud** | `*.atlassian.net` | `JIRA_EMAIL`, `JIRA_API_TOKEN` (create at id.atlassian.com → Security → API tokens) |
| **Data Center / Server** | your own domain | `JIRA_PAT` (Jira profile → Personal Access Tokens) |

Not sure which one you have? Open Jira in a browser and look at the URL.

## Register with Claude Code

```bash
claude mcp add jira-policy-mcp -- jira-policy-mcp --policy /absolute/path/to/policy.yml
```

Then `/mcp` in Claude Code should show the server connected, exposing only the tools your policy enables.

## Capabilities → tools

| Capability | Tool | Default |
|---|---|---|
| `read_by_key` | `jira_get_issue(key)` | ✅ on |
| `search` | `jira_search(jql, max_results)` | off |
| `read_comments` | `jira_get_comments(key)` | off |
| `add_comment` | `jira_add_comment(key, body)` | off |
| `create` | `jira_create_issue(project, issue_type, fields)` + `jira_get_create_fields(project, issue_type)` | off |
| `edit` | `jira_update_issue(key, fields)` | off |
| `transition` | `jira_transition_issue(key, transition)` | off |
| `delete` | `jira_delete_issue(key)` | off |

For `create`/`edit`, writable fields are restricted to `allowed_fields` unless `allow_all_fields: true` is set. `jira_get_create_fields` lets the agent discover which fields a project/issue type accepts (with their type and allowed values) before creating. `create_defaults` forces fixed field values on every create (overriding the agent), e.g. always setting a "Team" field.

### Rich-text fields (description, comment body)

On Cloud, agents pass `description` (and other rich-text fields) as a **Markdown string** — the server converts it to ADF before sending to Jira. Supported: paragraphs, headings, `**bold**`, `*italic*`, `` `inline code` ``, fenced/indented code blocks (with language), bullet/ordered lists, links, blockquotes, hard breaks, and horizontal rules. Pre-built ADF dicts are also accepted and passed through unchanged.

On Data Center/Server, rich-text fields are sent verbatim — write the wiki markup the deployment expects.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## License

MIT
