"""Wiki-markup detection tests — ensures rich-text input on Cloud rejects
Jira wiki markup with a helpful Markdown-equivalent hint, instead of
silently shipping a ticket whose `{{...}}` / `{code}…{code}` etc. render
literally because the Markdown-to-ADF converter passes them through verbatim.
"""

from __future__ import annotations

import pytest

from jira_policy_mcp.jira_client import JiraError, _detect_wiki_markup


# --- _detect_wiki_markup (pure) ----------------------------------------------


def test_empty_input_yields_no_findings():
    assert _detect_wiki_markup("") == []


def test_plain_markdown_passes():
    assert _detect_wiki_markup("Use `Foo` and **bold**.") == []


def test_template_braces_with_spaces_dont_false_positive():
    # Mustache / Jinja / Rust templates legitimately use `{{ ... }}` with
    # spaces or punctuation inside — must not be flagged as wiki markup.
    assert _detect_wiki_markup("Render {{ post.title }} and {{ user|safe }}.") == []


def test_double_brace_identifier_flagged():
    findings = _detect_wiki_markup("See {{CollectionOverviewView}} for details.")
    assert len(findings) == 1
    assert findings[0].startswith("{{identifier}}")
    assert "`identifier`" in findings[0]


def test_code_block_flagged():
    findings = _detect_wiki_markup("{code:swift}let x = 1{code}")
    assert any("{code}…{code}" in f for f in findings)


def test_noformat_flagged():
    findings = _detect_wiki_markup("before {noformat}raw{noformat} after")
    assert any("{noformat}" in f for f in findings)


def test_quote_block_flagged():
    findings = _detect_wiki_markup("{quote}quoted{quote}")
    assert any("{quote}" in f for f in findings)


def test_color_wrapper_flagged():
    findings = _detect_wiki_markup("{color:red}oops{color}")
    assert any("{color" in f for f in findings)


def test_bq_line_leading_flagged():
    findings = _detect_wiki_markup("bq. a Jira-style blockquote")
    assert any("bq." in f for f in findings)


def test_bq_inline_does_not_false_positive():
    # "bq." not at line start — e.g. mid-sentence — should not flag.
    assert _detect_wiki_markup("we use bq.foo as a prefix") == []


def test_heading_line_leading_flagged():
    findings = _detect_wiki_markup("h2. My Section\nbody")
    assert any("h1./h2./…" in f for f in findings)


def test_multiple_findings_collected():
    text = "See {{Foo}}.\n\n{code}x{code}\n\nbq. quoted"
    findings = _detect_wiki_markup(text)
    # Three distinct categories should be reported.
    assert len(findings) == 3


# --- _body_field integration (rejects on Cloud, passes through on DC) --------


class _DummyClient:
    """Minimal stand-in for JiraClient that exposes ``_body_field`` without
    touching httpx — the method only depends on ``_is_cloud``.
    """

    def __init__(self, *, is_cloud: bool):
        self._is_cloud = is_cloud

    # copy the method under test verbatim by binding it from the class
    from jira_policy_mcp.jira_client import JiraClient

    _body_field = JiraClient._body_field


def test_body_field_rejects_wiki_markup_on_cloud():
    client = _DummyClient(is_cloud=True)
    with pytest.raises(JiraError) as exc:
        client._body_field("See {{CollectionOverviewView}} for details.")
    msg = str(exc.value)
    assert "Markdown" in msg
    assert "{{identifier}}" in msg


def test_body_field_accepts_plain_markdown_on_cloud():
    client = _DummyClient(is_cloud=True)
    out = client._body_field("Use `Foo` and **bold**.")
    # On Cloud we get an ADF doc back — just check the envelope.
    assert isinstance(out, dict)
    assert out["type"] == "doc"


def test_body_field_passes_wiki_markup_through_on_data_center():
    # On DC/Server, wiki markup is the expected input — no detection runs.
    client = _DummyClient(is_cloud=False)
    out = client._body_field("See {{CollectionOverviewView}}.")
    assert out == "See {{CollectionOverviewView}}."
