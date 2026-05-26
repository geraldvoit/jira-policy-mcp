"""Markdown -> ADF conversion tests."""

from __future__ import annotations

from jira_policy_mcp.adf import markdown_to_adf


def test_empty_input_yields_empty_paragraph_doc():
    assert markdown_to_adf("") == {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": []}],
    }


def test_plain_paragraph():
    doc = markdown_to_adf("hello world")
    assert doc["content"] == [
        {"type": "paragraph", "content": [{"type": "text", "text": "hello world"}]}
    ]


def test_two_paragraphs():
    doc = markdown_to_adf("one\n\ntwo")
    assert [block["type"] for block in doc["content"]] == ["paragraph", "paragraph"]
    assert doc["content"][0]["content"][0]["text"] == "one"
    assert doc["content"][1]["content"][0]["text"] == "two"


def test_bold_em_inline_code():
    doc = markdown_to_adf("**bold** and *italic* and `code`")
    para = doc["content"][0]
    assert para["type"] == "paragraph"
    texts = [(c["text"], [m["type"] for m in c.get("marks", [])]) for c in para["content"]]
    assert ("bold", ["strong"]) in texts
    assert ("italic", ["em"]) in texts
    assert ("code", ["code"]) in texts


def test_inline_code_with_special_chars_kept_verbatim():
    doc = markdown_to_adf("call `foo(x)` here")
    para = doc["content"][0]
    code_node = [c for c in para["content"] if "marks" in c][0]
    assert code_node["text"] == "foo(x)"
    assert code_node["marks"] == [{"type": "code"}]


def test_fenced_code_block_with_language():
    doc = markdown_to_adf("```swift\nlet x = 1\n```")
    block = doc["content"][0]
    assert block == {
        "type": "codeBlock",
        "attrs": {"language": "swift"},
        "content": [{"type": "text", "text": "let x = 1"}],
    }


def test_fenced_code_block_without_language():
    doc = markdown_to_adf("```\nplain\n```")
    block = doc["content"][0]
    assert block["type"] == "codeBlock"
    assert "attrs" not in block
    assert block["content"] == [{"type": "text", "text": "plain"}]


def test_bullet_list():
    doc = markdown_to_adf("- one\n- two")
    block = doc["content"][0]
    assert block["type"] == "bulletList"
    assert len(block["content"]) == 2
    first_item = block["content"][0]
    assert first_item["type"] == "listItem"
    assert first_item["content"][0]["type"] == "paragraph"
    assert first_item["content"][0]["content"][0]["text"] == "one"


def test_ordered_list_with_custom_start():
    doc = markdown_to_adf("3. third\n4. fourth")
    block = doc["content"][0]
    assert block["type"] == "orderedList"
    assert block["attrs"] == {"order": 3}


def test_ordered_list_default_start_omits_attrs():
    doc = markdown_to_adf("1. one\n2. two")
    block = doc["content"][0]
    assert block["type"] == "orderedList"
    assert "attrs" not in block


def test_heading_levels():
    doc = markdown_to_adf("# H1\n\n## H2\n\n### H3")
    levels = [b["attrs"]["level"] for b in doc["content"] if b["type"] == "heading"]
    assert levels == [1, 2, 3]


def test_link_mark():
    doc = markdown_to_adf("see [docs](https://example.com)")
    para = doc["content"][0]
    link_node = [c for c in para["content"] if c.get("marks")][0]
    assert link_node["text"] == "docs"
    assert link_node["marks"] == [
        {"type": "link", "attrs": {"href": "https://example.com"}}
    ]


def test_blockquote():
    doc = markdown_to_adf("> quoted line")
    bq = doc["content"][0]
    assert bq["type"] == "blockquote"
    assert bq["content"][0]["type"] == "paragraph"
    assert bq["content"][0]["content"][0]["text"] == "quoted line"


def test_horizontal_rule():
    doc = markdown_to_adf("above\n\n---\n\nbelow")
    types = [b["type"] for b in doc["content"]]
    assert "rule" in types


def test_kids_1062_renders_as_structured_adf():
    """End-to-end: the body from the bug report should produce structured ADF,
    not a single paragraph with literal markup."""
    body = (
        "TestFlight-Crash mit EXC_BAD_ACCESS.\n"
        "\n"
        "**Symbolifizierter Stack (Build 1255):**\n"
        "\n"
        "```\n"
        "Thread 9 (crashing):\n"
        "  outlined assign with copy of KidsAppSettings\n"
        "```\n"
        "\n"
        "**Ursache:** `static var shared { didSet { saveToUserDefaults() } }` "
        "ist nicht thread-safe.\n"
        "\n"
        "**Crashpoints:**\n"
        "- D8g3G0E1uPC9UHsp91w2zB (SwiftUICore-Render, low impact)\n"
        "- Dc0kK847wd1N2av6lPASwZ (NO_CRASH_STACK, high impact)\n"
    )
    doc = markdown_to_adf(body)
    block_types = [b["type"] for b in doc["content"]]
    assert "paragraph" in block_types
    assert "codeBlock" in block_types
    assert "bulletList" in block_types

    # Every "**foo**" run should produce a strong-marked text node, not literal asterisks.
    flat_text = _flatten_text(doc)
    assert "**" not in flat_text
    assert "Symbolifizierter Stack" in flat_text
    assert _has_mark(doc, "strong", "Ursache:")

    # Code block content must be preserved verbatim, no leading/trailing fences.
    code_block = next(b for b in doc["content"] if b["type"] == "codeBlock")
    assert "Thread 9 (crashing):" in code_block["content"][0]["text"]
    assert "```" not in code_block["content"][0]["text"]


def test_softbreak_becomes_space():
    """A single newline inside a paragraph should not render as a literal newline."""
    doc = markdown_to_adf("line one\nline two")
    para = doc["content"][0]
    joined = "".join(c.get("text", "") for c in para["content"])
    assert "line one line two" == joined


def _flatten_text(doc: dict) -> str:
    parts: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                parts.append(node.get("text", ""))
            for child in node.get("content", []) or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(doc)
    return "".join(parts)


def _has_mark(doc: dict, mark_type: str, contains_text: str) -> bool:
    found = {"value": False}

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and contains_text in node.get("text", ""):
                if any(m.get("type") == mark_type for m in node.get("marks", [])):
                    found["value"] = True
            for child in node.get("content", []) or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(doc)
    return found["value"]
