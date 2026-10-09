"""Typed display blocks for `present`.

Tasks decide *what* to show for an item (`present_item`); this module owns *how*: escaping,
control-character stripping, size caps and layout. Blocks are plain JSON-able dicts so the
page remains a pure rendering of `data.json`.

Block types: `pre`, `code` (capped excerpt), `image`, `files`, `table`, `notice`, `scores`.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .packaging import SECRET_PATTERNS

MAX_EXCERPT_LINES = 200
MAX_EXCERPT_BYTES = 64 * 1024
MAX_LINE_CHARS = 2000
MAX_COPIED_BYTES = 256 * 1024

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_UNSAFE_SCHEME = re.compile(r"(?i)\b(?:javascript|data|vbscript)\s*:")


def sanitize_text(value: str) -> str:
    """Strip terminal escapes and control bytes, defuse script URLs, redact secret-shaped text."""
    cleaned = _CONTROL.sub("", _ANSI.sub("", value.replace("\r\n", "\n").replace("\r", "\n")))
    cleaned = _UNSAFE_SCHEME.sub("[unsafe-link-removed]", cleaned)
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub("[secret-redacted]", cleaned)
    return cleaned


def cap_excerpt(
    text: str, *, max_lines: int = MAX_EXCERPT_LINES, max_bytes: int = MAX_EXCERPT_BYTES
) -> tuple[str, dict[str, int]]:
    """Keep the first and last halves of an oversized text; report what was omitted."""
    lines = [
        line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " …[line truncated]"
        for line in sanitize_text(text).split("\n")
    ]
    omitted = 0
    if len(lines) > max_lines:
        head = max_lines // 2
        tail = max_lines - head
        omitted = len(lines) - max_lines
        lines = [*lines[:head], f"… {omitted} line(s) omitted …", *lines[-tail:]]
    result = "\n".join(lines)
    omitted_bytes = 0
    encoded = result.encode("utf-8")
    if len(encoded) > max_bytes:
        omitted_bytes = len(encoded) - max_bytes
        half = max_bytes // 2
        result = (
            encoded[:half].decode("utf-8", errors="ignore")
            + f"\n… {omitted_bytes} byte(s) omitted …\n"
            + encoded[-half:].decode("utf-8", errors="ignore")
        )
    return result, {"omitted_lines": omitted, "omitted_bytes": omitted_bytes}


# -- constructors ------------------------------------------------------------------------


def pre(title: str, text: Any) -> dict[str, Any]:
    return {"type": "pre", "title": title, "text": sanitize_text(_stringify(text))}


def json_pre(title: str, value: Any) -> dict[str, Any]:
    return pre(title, json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2))


def code(title: str, text: str, *, note: str | None = None) -> dict[str, Any]:
    if not text.strip():
        note, text = (note + " " if note else "") + "(empty: the program wrote nothing here)", ""
    capped, omitted = cap_excerpt(text)
    return {"type": "code", "title": title, "text": capped, "omitted": omitted, "note": note}


def image(title: str, src: str | None, alt: str, missing: str | None = None) -> dict[str, Any]:
    return {"type": "image", "title": title, "src": src, "alt": alt, "missing": missing}


def files(title: str, links: list[dict[str, Any]]) -> dict[str, Any]:
    """links: [{name, href|None, size, note}]; href is relative to the site root."""
    return {"type": "files", "title": title, "links": links}


def table(title: str, headers: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"type": "table", "title": title, "headers": headers, "rows": [[_stringify(c) for c in row] for row in rows]}


def notice(text: str) -> dict[str, Any]:
    return {"type": "notice", "text": sanitize_text(text)}


def _stringify(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


# -- item view ---------------------------------------------------------------------------


@dataclass
class ItemView:
    """What a task shows for one item. `success` feeds the run row's success count."""

    column_label: str
    column_value: str
    success: bool | None = None
    headline: str | None = None
    grid: list[list[dict[str, Any]]] = field(default_factory=list)  # side-by-side cells, each a block list
    sections: list[dict[str, Any]] = field(default_factory=list)  # full-width blocks below the grid
    copy: list[dict[str, str]] = field(default_factory=list)  # {"source": abs path, "target": site path}

    def as_dict(self) -> dict[str, Any]:
        return {
            "column_label": self.column_label,
            "column_value": self.column_value,
            "success": self.success,
            "headline": self.headline,
            "grid": self.grid,
            "sections": self.sections,
        }


@dataclass(frozen=True)
class ItemContext:
    """Everything a task presenter may read. It never gets a provider or an environment."""

    eval_dir: Path
    case_dir: Path
    item_dir: Path
    run_id: str
    result: dict[str, Any]
    item: dict[str, Any]
    report_entry: dict[str, Any]
    include_text_artifacts: bool = True

    def read_artifact_text(self, relative: str) -> str | None:
        """Text of a stored artifact addressed relative to the eval dir; None if unreadable."""
        path = (self.eval_dir / relative).resolve()
        try:
            path.relative_to(self.item_dir.resolve())
        except ValueError:
            return None
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            data = stream.read(MAX_COPIED_BYTES * 4)
        return data.decode("utf-8", errors="replace")


# -- rendering ---------------------------------------------------------------------------


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def render_blocks(blocks: list[dict[str, Any]], *, up: str = "../") -> str:
    return "".join(_render_block(block, up) for block in blocks)


def _title(block: dict[str, Any]) -> str:
    return f"<h3>{_e(block['title'])}</h3>" if block.get("title") else ""


def _render_block(block: dict[str, Any], up: str) -> str:
    kind = block.get("type")
    if kind == "pre":
        return f"{_title(block)}<pre>{_e(block.get('text'))}</pre>"
    if kind == "code":
        omitted = block.get("omitted") or {}
        note = block.get("note") or ""
        if omitted.get("omitted_lines") or omitted.get("omitted_bytes"):
            note = (note + " " if note else "") + "Shown with the middle omitted; the full file is linked in the files list."
        note_html = f'<p class="muted">{_e(note)}</p>' if note else ""
        return f'{_title(block)}{note_html}<pre class="excerpt">{_e(block.get("text"))}</pre>'
    if kind == "image":
        if block.get("src"):
            body = f'<img class="item-image" src="{_e(up)}{_e(block["src"])}" alt="{_e(block.get("alt"))}">'
        else:
            body = f"<p>{_e(block.get('missing') or 'No image.')}</p>"
        return f"{_title(block)}{body}"
    if kind == "files":
        items = []
        for link in block.get("links", []):
            size = f" <span class=\"muted\">({_e(link.get('size'))} bytes)</span>" if link.get("size") is not None else ""
            note = f" <span class=\"muted\">{_e(link['note'])}</span>" if link.get("note") else ""
            if link.get("href"):
                items.append(f'<li><a href="{_e(up)}{_e(link["href"])}">{_e(link["name"])}</a>{size}{note}</li>')
            else:
                items.append(f"<li>{_e(link['name'])}{size}{note}</li>")
        return f"{_title(block)}<ul>{''.join(items)}</ul>" if items else ""
    if kind == "table":
        head = "".join(f"<th>{_e(h)}</th>" for h in block.get("headers", []))
        body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>" for row in block.get("rows", []))
        return f'{_title(block)}<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    if kind == "notice":
        return f'<p class="notice">{_e(block.get("text"))}</p>'
    return ""


def render_item_view(view: dict[str, Any], *, up: str = "../") -> str:
    grid = "".join(f"<div>{render_blocks(cell, up=up)}</div>" for cell in view.get("grid", []))
    grid_html = f'<div class="evidence-details-grid">{grid}</div>' if grid else ""
    return f'<div class="evidence-details">{grid_html}{render_blocks(view.get("sections", []), up=up)}</div>'
