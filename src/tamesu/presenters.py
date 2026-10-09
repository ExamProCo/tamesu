"""How each task's items look on the page.

Deliberately NOT in the task modules: `artifact_bundle.py` and `image_generation.py` are part of
a run's identity hash, so editing how a page looks must never invalidate banked evidence.
"""
from __future__ import annotations

from typing import Any, Callable

def present_structured_text(ctx: Any) -> Any:
    from . import present_blocks as blocks

    scores = ctx.result.get("mechanical_scores", ctx.result.get("scores", {}))
    scores = scores if isinstance(scores, dict) else {}
    exact = scores.get("exact_match")
    output_path = ctx.item_dir / "output.txt"
    output = (
        output_path.read_text(encoding="utf-8", errors="replace") if output_path.is_file() else None
    )
    return blocks.ItemView(
        column_label="Exact match",
        column_value="Yes" if exact is True else "No" if exact is False else "—",
        success=exact is True,
        grid=[
            [blocks.json_pre("Input", ctx.item.get("input"))],
            [blocks.json_pre("Expected", ctx.item.get("expected"))],
            [blocks.pre("Output", output or "No stored output")],
        ],
        sections=[blocks.json_pre("Scores", scores)],
    )


def present_image_review_item(ctx: Any, blocks: Any) -> Any:
    """Item view for results shaped like an image generation: image plus separate evidence."""
    result, entry = ctx.result, ctx.report_entry
    output = result.get("output") if isinstance(result.get("output"), dict) else None
    src = None
    copy: list[dict[str, str]] = []
    if output:
        source = ctx.item_dir / str(output["path"])
        if source.is_file() and source.resolve().is_relative_to(ctx.case_dir.resolve()):
            src = f"assets/images/{ctx.run_id}/{ctx.item_dir.name}{source.suffix}"
            copy.append({"source": str(source), "target": src})
    scores = result.get("mechanical_scores", {})
    human = entry.get("human") or {}
    acceptance = entry.get("acceptance") or {}
    evidence: dict[str, Any] = {
        "outcome": result.get("outcome"),
        "mechanical": scores,
        "model_judge": (entry.get("model_judge") or {}).get("verdict"),
        "human": human.get("verdict") or human.get("state"),
        "acceptance": acceptance,
    }
    revised = (result.get("provider_response") or {}).get("revised_prompt")
    if revised:
        evidence["revised_prompt"] = revised
    reason = (result.get("outcome") or {}).get("reason")
    return blocks.ItemView(
        column_label="Acceptance",
        column_value=str(acceptance.get("state") or "—").title(),
        success=acceptance.get("state") == "accepted",
        grid=[
            [blocks.image("Image", src, ctx.item_dir.name, missing=f"No image ({reason}).")],
            [blocks.json_pre("Input", ctx.item.get("input"))],
            [blocks.json_pre("Evidence (separate columns, never blended)", evidence)],
        ],
        copy=copy,
    )


def present_image_generation(ctx: Any) -> Any:
    from . import present_blocks as blocks

    return present_image_review_item(ctx, blocks)


def present_artifact_bundle(ctx: Any) -> Any:
    """Scores, run facts, capped logs, source links and a failure headline for one item."""
    import re

    from . import present_blocks as blocks

    result = ctx.result
    scores = result.get("mechanical_scores", {}) if isinstance(result.get("mechanical_scores"), dict) else {}
    artifacts = [a for a in result.get("artifacts", []) if isinstance(a, dict)]
    accepted = (ctx.report_entry.get("evidence") or {}).get("acceptance") if ctx.report_entry else None
    has_review = "outcome" in result  # set only when a review artifact is declared
    copy: list[dict[str, str]] = []
    text_blocks: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    first_error: str | None = None
    error_line = re.compile(r"(?i)\b(error|fatal|exception|traceback)\b")

    def target_for(artifact: dict[str, Any]) -> str | None:
        marker = "/artifacts/"
        path = str(artifact.get("path", ""))
        if marker not in path:
            return None
        name = path.split(marker, 1)[1]
        if not name or name.startswith("/") or ".." in name.split("/"):
            return None
        return f"assets/artifacts/{ctx.run_id}/{ctx.item_dir.name}/{name}"

    for artifact in artifacts:
        media = str(artifact.get("media_type", ""))
        role = str(artifact.get("role"))
        source = ctx.eval_dir / str(artifact.get("path", ""))
        target = target_for(artifact)
        if media.startswith("image/") and target and source.is_file():
            copy.append({"source": str(source), "target": target})
            continue
        if not media.startswith("text/") or not ctx.include_text_artifacts or not target:
            continue
        if role in {"source", "response"}:
            href = target + ".txt"
            copy.append({"source": str(source), "target": href, "max_bytes": str(blocks.MAX_COPIED_BYTES)})
            links.append({"name": str(artifact["path"]).split("/artifacts/", 1)[1], "href": href, "size": artifact.get("size"), "note": role if role != "source" else None})
            continue
        text = ctx.read_artifact_text(str(artifact["path"]))
        if text is None:
            continue
        if first_error is None:
            for line in text.splitlines():
                if error_line.search(line):
                    first_error = blocks.sanitize_text(line.strip())[:200]
                    break
        href = target + ".txt"
        copy.append({"source": str(source), "target": href, "max_bytes": str(blocks.MAX_COPIED_BYTES)})
        links.append({"name": str(artifact["path"]).split("/artifacts/", 1)[1], "href": href, "size": artifact.get("size"), "note": role})
        text_blocks.append(blocks.code(role, text))

    failing = [(name, s) for name, s in scores.items() if isinstance(s, dict) and s.get("pass") is not True]
    headline: str | None = None
    if result.get("state") == "failed":
        headline = f"did not complete: {(result.get('error') or {}).get('message', 'unknown error')}"
    elif failing:
        name, record = failing[0]
        detail = record.get("answer") or "nothing passed"
        headline = f"{name} scored {record.get('value')!s} (passed: {detail})"
        if first_error:
            headline += f" — first error: {first_error}"
    elif result.get("missing_artifacts"):
        headline = f"missing required artifact(s): {', '.join(result['missing_artifacts'])}"
    if (result.get("outcome") or {}).get("status") == "invalid_artifact" and headline is None:
        headline = f"no reviewable image ({result['outcome'].get('reason')})"

    score_rows = [
        [name, record.get("value"), "pass" if record.get("pass") else "fail", record.get("answer") or record.get("explanation") or ""]
        for name, record in scores.items()
        if isinstance(record, dict)
    ]
    usage = (result.get("generation") or {}).get("usage") or {}
    inspect = result.get("inspect") or {}
    facts = [
        ["stop reason", inspect.get("stop_reason") or "—"],
        ["limit hit", (inspect.get("limit") or {}).get("type") or "none"],
        ["tokens in / out", f"{usage.get('input_tokens', '—')} / {usage.get('output_tokens', '—')}"],
        ["reasoning tokens", usage.get("reasoning_tokens", "—")],
        ["latency ms", (result.get("generation") or {}).get("latency_ms", "—")],
        ["cost usd", (result.get("generation") or {}).get("cost_usd", "—")],
    ]
    sections: list[dict[str, Any]] = []
    if result.get("missing_artifacts"):
        sections.append(blocks.notice("Required artifact role(s) never arrived: " + ", ".join(result["missing_artifacts"])))
    if result.get("rejected_artifacts"):
        sections.append(blocks.table("Rejected artifacts", ["name", "reason"], [[r.get("name"), r.get("reason")] for r in result["rejected_artifacts"]]))
    sections.append(blocks.table("Run", ["fact", "value"], facts))
    sections.extend(text_blocks)
    if links:
        sections.append(blocks.files("Files", links))
    elif artifacts and not ctx.include_text_artifacts:
        sections.append(blocks.notice("Source and logs are withheld from this rendering."))

    grid: list[list[dict[str, Any]]] = []
    if has_review:
        output = result.get("output") or {}
        reason = (result.get("outcome") or {}).get("reason")
        shown = None
        if output:
            candidate = ctx.item_dir / str(output.get("path", ""))
            match = next((c for c in copy if c["source"] == str(candidate)), None)
            shown = match["target"] if match else None
        grid.append([blocks.image("Render", shown, ctx.item_dir.name, missing=f"No image ({reason}).")])
    grid.append([blocks.json_pre("Input", ctx.item.get("input"))])
    grid.append([blocks.table("Scores", ["scorer", "value", "result", "detail"], score_rows)])
    if has_review:
        state = (accepted or {}).get("state")
        label, value, success = "Acceptance", str(state or "—").title(), state == "accepted"
    else:
        label = "Score"
        numeric = [r.get("value") for r in scores.values() if isinstance(r, dict) and isinstance(r.get("value"), (int, float))]
        value = f"{sum(numeric) / len(numeric):.2f}" if numeric else "—"
        success = bool(scores) and not failing
    return blocks.ItemView(label, value, success, headline, grid, sections, copy)


PRESENTERS: dict[str, Callable[[Any], Any]] = {
    "structured_text": present_structured_text,
    "image_generation": present_image_generation,
    "artifact_bundle": present_artifact_bundle,
}


def present_item(task_name: str, ctx: Any) -> Any:
    return PRESENTERS[task_name](ctx)
