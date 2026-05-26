"""Markdown -> Atlassian Document Format conversion.

The Jira Cloud REST API stores rich-text fields (description, comment bodies)
as ADF documents, not strings. The MCP exposes a Markdown-friendly interface
to agents; this module bridges the two so callers can write idiomatic Markdown
and let Jira render it natively instead of seeing literal `*bold*` text.

Supported block nodes: doc, paragraph, heading (h1-h6), codeBlock (fenced and
indented), blockquote, bulletList, orderedList, listItem, rule.
Supported inline nodes: text, hardBreak.
Supported marks: strong, em, code, link.

Anything we don't recognize is flattened into plain text so callers never lose
content silently.
"""

from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode


_md = MarkdownIt("commonmark", {"breaks": False, "html": False})


def markdown_to_adf(text: str) -> dict:
    """Parse a Markdown string and return a Jira Cloud ADF document."""
    tokens = _md.parse(text or "")
    tree = SyntaxTreeNode(tokens)
    blocks = [node for node in (_block(child) for child in tree.children) if node]
    if not blocks:
        blocks = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": blocks}


def _block(node: SyntaxTreeNode) -> dict | None:
    kind = node.type

    if kind == "paragraph":
        return {"type": "paragraph", "content": _inline(node)}

    if kind == "heading":
        level = int(node.tag[1:])
        return {
            "type": "heading",
            "attrs": {"level": level},
            "content": _inline(node),
        }

    if kind == "fence":
        block: dict = {"type": "codeBlock"}
        language = (node.info or "").strip().split(None, 1)[0] if node.info else ""
        if language:
            block["attrs"] = {"language": language}
        body = (node.content or "").rstrip("\n")
        if body:
            block["content"] = [{"type": "text", "text": body}]
        return block

    if kind == "code_block":
        block = {"type": "codeBlock"}
        body = (node.content or "").rstrip("\n")
        if body:
            block["content"] = [{"type": "text", "text": body}]
        return block

    if kind == "bullet_list":
        return {
            "type": "bulletList",
            "content": [
                item for item in (_block(c) for c in node.children if c.type == "list_item") if item
            ],
        }

    if kind == "ordered_list":
        out: dict = {
            "type": "orderedList",
            "content": [
                item for item in (_block(c) for c in node.children if c.type == "list_item") if item
            ],
        }
        start = _attr(node, "start")
        if start and int(start) != 1:
            out["attrs"] = {"order": int(start)}
        return out

    if kind == "list_item":
        children = [n for n in (_block(c) for c in node.children) if n]
        if not children:
            children = [{"type": "paragraph", "content": []}]
        return {"type": "listItem", "content": children}

    if kind == "blockquote":
        inner = [n for n in (_block(c) for c in node.children) if n]
        if not inner:
            inner = [{"type": "paragraph", "content": []}]
        return {"type": "blockquote", "content": inner}

    if kind == "hr":
        return {"type": "rule"}

    return None


def _inline(node: SyntaxTreeNode) -> list[dict]:
    """Return ADF inline content for a block node carrying inline children."""
    children: list[SyntaxTreeNode]
    if len(node.children) == 1 and node.children[0].type == "inline":
        children = node.children[0].children
    else:
        children = node.children
    out: list[dict] = []
    for child in children:
        _walk_inline(child, (), out)
    return out


def _walk_inline(node: SyntaxTreeNode, marks: tuple, out: list[dict]) -> None:
    kind = node.type

    if kind == "text":
        if node.content:
            _append_text(out, node.content, marks)
        return

    if kind == "code_inline":
        _append_text(out, node.content or "", marks + ({"type": "code"},))
        return

    if kind == "softbreak":
        _append_text(out, " ", ())
        return

    if kind == "hardbreak":
        out.append({"type": "hardBreak"})
        return

    if kind == "strong":
        for child in node.children:
            _walk_inline(child, marks + ({"type": "strong"},), out)
        return

    if kind == "em":
        for child in node.children:
            _walk_inline(child, marks + ({"type": "em"},), out)
        return

    if kind == "link":
        href = _attr(node, "href") or ""
        link_mark = {"type": "link", "attrs": {"href": href}}
        for child in node.children:
            _walk_inline(child, marks + (link_mark,), out)
        return

    for child in node.children:
        _walk_inline(child, marks, out)


def _append_text(out: list[dict], text: str, marks: tuple) -> None:
    entry: dict = {"type": "text", "text": text}
    if marks:
        entry["marks"] = [dict(m) for m in marks]
    out.append(entry)


def _attr(node: SyntaxTreeNode, key: str):
    attrs = node.attrs
    if not attrs:
        return None
    return attrs.get(key)
