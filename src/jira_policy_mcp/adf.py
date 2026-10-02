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


# -- ADF -> Markdown (read direction) ------------------------------------------
# Raw ADF is several times larger than its text; agents only need the content.


def adf_to_markdown(doc) -> str:
    """Render an ADF document as Markdown. Strings (Data Center) pass through."""
    if not isinstance(doc, dict):
        return doc or ""
    return "\n\n".join(b for b in (_md_block(n) for n in doc.get("content") or []) if b)


def _md_block(node: dict, depth: int = 0) -> str:
    kind = node.get("type")
    children = node.get("content") or []
    if kind == "paragraph":
        return _md_inline(children)
    if kind == "heading":
        level = (node.get("attrs") or {}).get("level", 1)
        return "#" * level + " " + _md_inline(children)
    if kind == "codeBlock":
        lang = (node.get("attrs") or {}).get("language") or ""
        return f"```{lang}\n{_md_inline(children)}\n```"
    if kind == "blockquote":
        inner = "\n\n".join(_md_block(c, depth) for c in children)
        return "\n".join("> " + line for line in inner.splitlines())
    if kind in ("bulletList", "orderedList"):
        lines = []
        for i, item in enumerate(children, start=1):
            marker = f"{i}." if kind == "orderedList" else "-"
            parts = [_md_block(c, depth + 1) for c in item.get("content") or []]
            text = "\n".join(p for p in parts if p)
            indent = "  " * depth
            lines.append(f"{indent}{marker} {text.lstrip()}")
        return "\n".join(lines)
    if kind == "rule":
        return "---"
    if kind == "table":
        rows = [[_md_cell(cell) for cell in row.get("content") or []] for row in children]
        if rows:
            rows.insert(1, ["---"] * len(rows[0]))
        return "\n".join("| " + " | ".join(row) + " |" for row in rows)
    if kind in ("mediaSingle", "mediaGroup", "media"):
        return "[attachment]"
    # Panels, expands and unknown blocks: keep their text.
    return "\n\n".join(b for b in (_md_block(c, depth) for c in children) if b)


def _md_cell(cell: dict) -> str:
    return " ".join(_md_block(c) for c in cell.get("content") or []).replace("\n", " ")


def _md_inline(nodes: list) -> str:
    out = []
    for node in nodes:
        kind = node.get("type")
        attrs = node.get("attrs") or {}
        if kind == "text":
            out.append(_md_marks(node.get("text", ""), node.get("marks") or []))
        elif kind == "hardBreak":
            out.append("\n")
        elif kind == "mention":
            out.append(attrs.get("text") or "@user")
        elif kind == "emoji":
            out.append(attrs.get("text") or attrs.get("shortName") or "")
        elif kind in ("inlineCard", "blockCard"):
            out.append(attrs.get("url") or "")
        elif kind == "status":
            out.append(f"[{attrs.get('text', '')}]")
        elif kind == "date":
            out.append(str(attrs.get("timestamp", "")))
        else:
            out.append(_md_inline(node.get("content") or []))
    return "".join(out)


def _md_marks(text: str, marks: list) -> str:
    for mark in marks:
        kind = mark.get("type")
        if kind == "code":
            text = f"`{text}`"
        elif kind == "strong":
            text = f"**{text}**"
        elif kind == "em":
            text = f"*{text}*"
        elif kind == "strike":
            text = f"~~{text}~~"
        elif kind == "link":
            text = f"[{text}]({(mark.get('attrs') or {}).get('href', '')})"
    return text
