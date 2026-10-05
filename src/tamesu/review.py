"""Human review: export a blinded pack, review anywhere, import immutable decisions.

Reviewers see images in shuffled order under random presentation IDs. The mapping back to
{run, item, image checksum} lives in a key file kept outside the pack, so a reviewer cannot
learn which arm or model produced an image. Reviewer identity is a free-text ID, not
authentication: this is a lab notebook, not an audit system.
"""

from __future__ import annotations

import io
import random
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from .artifacts import write_yaml, write_yaml_exclusive
from .config import load_yaml, resolve_contained
from .errors import ExecutionError
from .evidence import RunItem, select_generated_items
from .identity import digest_bytes, digest_file
from .models import EvalContext
from .policy import Acceptance, resolved_acceptance
from .rubric import NO_REASON, OTHER_REASON, Rubric, load_rubric, overall_pass

PACK_MODES = ("absolute",)  # "comparison" is reserved for the later scanner ablation
REVIEWER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Metadata keys that could name a generator; their presence is reported, never silently stripped.
IDENTIFYING_METADATA = {"software", "parameters", "prompt", "model", "generator", "comment", "c2pa", "xmp"}


def load_review_rubric(context: EvalContext) -> Rubric:
    block = context.evaluation.get("evaluation", {}).get("human_review")
    if not block:
        raise ExecutionError("This eval declares no evaluation.human_review block.")
    return load_rubric(resolve_contained(context.eval_dir, block["rubric"], context.project_root))


def keys_dir(context: EvalContext) -> Path:
    return context.eval_dir / "review" / "keys"


def reviews_dir(item_dir: Path) -> Path:
    return item_dir / "reviews"


# -- stored reviews ----------------------------------------------------------------------


def load_reviews(item_dir: Path) -> list[dict[str, Any]]:
    directory = reviews_dir(item_dir)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("*.yml")):
        record = load_yaml(path)
        record["_path"] = path
        records.append(record)
    return records


def split_reviews(
    records: list[dict[str, Any]], *, image_sha256: str, rubric_sha256: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(current, stale). Current = valid for this image and rubric, latest per reviewer."""
    valid, stale = [], []
    for record in records:
        if record.get("image_sha256") == image_sha256 and record.get("rubric", {}).get("sha256") == rubric_sha256:
            valid.append(record)
        else:
            stale.append(record)
    latest: dict[str, dict[str, Any]] = {}
    for record in sorted(valid, key=lambda r: str(r["submitted_at"])):
        latest[str(record["reviewer"])] = record
    return list(latest.values()), stale


def human_state(current: list[dict[str, Any]], acceptance: Acceptance) -> dict[str, Any]:
    """Human verdict from current reviews: pending, pass, fail, or an adjudication outcome.

    Disagreement is not an error. `reject` fails the item; `adjudicate` asks for one more
    review, and an odd number of reviews is decided by majority.
    """
    count = len(current)
    required = acceptance.required_reviews_per_item
    if count < required:
        return {"state": "pending", "verdict": None, "owed": required - count}
    passes = sum(1 for record in current if record["verdict"] == "pass")
    if passes == count:
        return {"state": "pass", "verdict": "pass", "owed": 0}
    if passes == 0:
        return {"state": "fail", "verdict": "fail", "owed": 0}
    if acceptance.on_disagreement == "reject":
        return {"state": "disagreement", "verdict": "fail", "owed": 0}
    if count % 2 == 1:
        return {"state": "adjudicated", "verdict": "pass" if passes > count - passes else "fail", "owed": 0}
    return {"state": "needs_adjudication", "verdict": None, "owed": 1}


# -- export ------------------------------------------------------------------------------


@dataclass
class ExportResult:
    pack_id: str
    out_dir: Path
    key_path: Path
    exported: int
    skipped: list[tuple[str, str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def export_pack(
    context: EvalContext,
    out_dir: Path,
    *,
    run_id: str | None = None,
    reviewer: str | None = None,
    seed: int | None = None,
    include_reviewed: bool = False,
    force: bool = False,
) -> ExportResult:
    rubric = load_review_rubric(context)
    acceptance = resolved_acceptance(context.evaluation["evaluation"])
    if reviewer is not None and not REVIEWER_PATTERN.fullmatch(reviewer):
        raise ExecutionError("Reviewer IDs may use letters, digits, '.', '_' and '-'.")
    replacing = out_dir.exists() and any(out_dir.iterdir())
    if replacing and not force:
        raise ExecutionError(
            f"Refusing to export into a non-empty directory: {out_dir}. "
            "Choose another --out, or pass --force to replace an earlier review pack."
        )
    if replacing and not (out_dir / "pack.yml").is_file():
        # --force only ever replaces a pack, never an arbitrary directory
        raise ExecutionError(f"{out_dir} does not look like a review pack (no pack.yml); not replacing it.")

    selection = select_generated_items(context, run_id)
    owed: list[RunItem] = []
    for entry in selection.items:
        current, _stale = split_reviews(
            load_reviews(entry.item_dir), image_sha256=entry.image_sha256, rubric_sha256=rubric.sha256
        )
        if include_reviewed or human_state(current, acceptance)["owed"]:
            owed.append(entry)
    if not owed:
        raise ExecutionError("No images are owed review. Use --all to export reviewed images again.")

    seed = seed if seed is not None else random.SystemRandom().getrandbits(48)
    rng = random.Random(seed)
    order = list(owed)
    rng.shuffle(order)
    created = datetime.now(UTC)
    pack_id = f"pack-{created.strftime('%Y%m%dT%H%M%SZ')}-{rng.getrandbits(16):04x}"

    result = ExportResult(pack_id, out_dir, keys_dir(context) / f"{pack_id}.yml", 0, list(selection.skipped))
    if replacing:
        # Deleted only now, after everything that can fail has passed, so a refused export
        # leaves the earlier pack intact. Its key file stays in review/keys/.
        shutil.rmtree(out_dir)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    pack_items: list[dict[str, Any]] = []
    key_items: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for entry in order:
        presentation_id = f"img-{rng.getrandbits(32):08x}"
        while presentation_id in used:
            presentation_id = f"img-{rng.getrandbits(32):08x}"
        used.add(presentation_id)
        data = entry.image_path.read_bytes()
        digest = digest_bytes(data)
        if digest != entry.image_sha256:
            result.skipped.append((entry.run_id, entry.item_id, "stale image"))
            continue
        leaks = _identifying_metadata(data)
        if leaks:
            result.warnings.append(
                f"{presentation_id}: image carries embedded metadata ({', '.join(sorted(leaks))}) "
                "that may identify the generator"
            )
        filename = f"{presentation_id}{entry.image_path.suffix}"
        shutil.copyfile(entry.image_path, images_dir / filename)
        pack_items.append(
            {"presentation_id": presentation_id, "image": f"images/{filename}", "input": entry.item.get("input", {})}
        )
        key_items[presentation_id] = {
            "run_id": entry.run_id,
            "item_id": entry.item_id,
            "image_sha256": digest,
        }
    result.exported = len(pack_items)

    pack_document = {
        "schema_version": 1,
        "pack_id": pack_id,
        "mode": "absolute",
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "rubric": {
            "name": rubric.name,
            "dimensions": [
                {"id": d.id, "question": d.question, "reason_codes": list(d.reason_codes)}
                for d in rubric.dimensions
            ],
        },
        "items": pack_items,
    }
    write_yaml(out_dir / "pack.yml", pack_document)
    (out_dir / "review.html").write_text(_review_html(pack_document), encoding="utf-8")
    write_yaml(
        out_dir / "responses.yml",
        {
            "schema_version": 1,
            "pack_id": pack_id,
            "reviewer": reviewer or "",
            "responses": [
                {
                    "presentation_id": item["presentation_id"],
                    "dimensions": {
                        d.id: {"pass": None, "reason_code": None} for d in rubric.dimensions
                    },
                    "notes": "",
                }
                for item in pack_items
            ],
        },
    )
    write_yaml(
        result.key_path,
        {
            "schema_version": 1,
            "pack_id": pack_id,
            "created_at": created.isoformat().replace("+00:00", "Z"),
            "seed": seed,
            "mode": "absolute",
            "rubric": {"name": rubric.name, "sha256": rubric.sha256},
            "items": key_items,
        },
    )
    return result


_REVIEW_HTML = """<!doctype html>
<meta charset="utf-8">
<title>Image review</title>
<style>
body{font:15px system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#222}
.item{border:1px solid #ccc;border-radius:8px;padding:1rem;margin:1rem 0}
img{max-width:100%;max-height:420px;display:block;margin-bottom:.5rem}
.dim{margin:.4rem 0}.dim label{margin-right:.8rem}
.brief{color:#555;font-size:13px;white-space:pre-wrap}
button{font:inherit;padding:.5rem 1rem}
</style>
<h1>Image review</h1>
<p>Reviewer ID: <input id="reviewer" placeholder="your-id"> &nbsp;
<button id="save">Download responses.yml</button> <span id="status"></span></p>
<p class="brief">Pass or fail every dimension for each image. Leave an image untouched to review it later.
Save the file next to the pack and run <code>tamesu review import</code>.</p>
<div id="items"></div>
<script>
const PACK = __PACK__;
const state = {};
const root = document.getElementById("items");
PACK.items.forEach((item) => {
  state[item.presentation_id] = {};
  const box = document.createElement("div"); box.className = "item";
  const img = document.createElement("img"); img.src = item.image; img.alt = item.presentation_id;
  const brief = document.createElement("div"); brief.className = "brief";
  brief.textContent = JSON.stringify(item.input, null, 2);
  const notes = document.createElement("input"); notes.placeholder = "notes (optional)"; notes.style.width = "100%";
  notes.addEventListener("input", () => { state[item.presentation_id].notes = notes.value; });
  box.append(img, brief);
  PACK.rubric.dimensions.forEach((dim) => {
    const row = document.createElement("div"); row.className = "dim";
    const name = document.createElement("strong"); name.textContent = dim.id + ": ";
    const q = document.createElement("span"); q.textContent = dim.question;
    const group = "d-" + item.presentation_id + "-" + dim.id;
    const make = (label, value) => {
      const l = document.createElement("label"); const r = document.createElement("input");
      r.type = "radio"; r.name = group;
      r.addEventListener("change", () => { sel.disabled = value; if (value) sel.value = ""; update(); });
      l.append(r, " " + label); l.dataset.value = value; return [l, r];
    };
    const [passL, passR] = make("pass", true), [failL, failR] = make("fail", false);
    const sel = document.createElement("select"); sel.disabled = true;
    sel.append(new Option("reason…", ""));
    dim.reason_codes.forEach((c) => sel.append(new Option(c, c)));
    sel.addEventListener("change", update);
    function update() {
      const d = state[item.presentation_id].dimensions ||= {};
      if (passR.checked) d[dim.id] = {pass: true, reason_code: null};
      else if (failR.checked) d[dim.id] = {pass: false, reason_code: sel.value || null};
    }
    row.append(name, q, document.createElement("br"), passL, failL, sel);
    box.append(row);
  });
  box.append(notes); root.append(box);
});
document.getElementById("save").addEventListener("click", () => {
  const responses = PACK.items.map((item) => {
    const s = state[item.presentation_id], dims = {};
    PACK.rubric.dimensions.forEach((dim) => {
      dims[dim.id] = (s.dimensions && s.dimensions[dim.id]) || {pass: null, reason_code: null};
    });
    return {presentation_id: item.presentation_id, dimensions: dims, notes: s.notes || ""};
  });
  const noReason = [];
  PACK.items.forEach((item) => {
    const d = (state[item.presentation_id] || {}).dimensions || {};
    Object.entries(d).forEach(([dim, o]) => {
      if (o.pass === false && !o.reason_code) noReason.push(item.presentation_id + " / " + dim);
    });
  });
  if (noReason.length) {
    document.getElementById("status").textContent =
      "Choose a reason for every fail: " + noReason.join(", ");
    return;
  }
  const missing = PACK.items.filter((item) => {
    const s = state[item.presentation_id], d = (s && s.dimensions) || {};
    return Object.values(d).some((o) => o.reason_code === "other") && !(s.notes || "").trim();
  });
  if (missing.length) {
    document.getElementById("status").textContent =
      "Add a note explaining 'other' for: " + missing.map((i) => i.presentation_id).join(", ");
    return;
  }
  const doc = {schema_version: 1, pack_id: PACK.pack_id,
               reviewer: document.getElementById("reviewer").value.trim(), responses};
  // JSON is valid YAML, so the import reads this file unchanged.
  const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], {type: "text/yaml"}));
  const a = document.createElement("a"); a.href = url; a.download = "responses.yml"; a.click();
  document.getElementById("status").textContent = "saved";
});
</script>
"""


def _review_html(pack: dict[str, Any]) -> str:
    import json

    payload = json.dumps(pack, ensure_ascii=False).replace("</", "<\\/")
    return _REVIEW_HTML.replace("__PACK__", payload)


def _identifying_metadata(data: bytes) -> set[str]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            keys = {str(key).lower() for key in image.info}
            if image.getexif():
                keys.add("exif")
    except Exception:
        return set()
    return {key for key in keys if key in IDENTIFYING_METADATA or key == "exif"}


# -- import ------------------------------------------------------------------------------


@dataclass
class ImportResult:
    pack_id: str
    reviewer: str
    written: list[Path] = field(default_factory=list)
    skipped_blank: int = 0


class ReviewImportError(ExecutionError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("Review import rejected; nothing was written:\n" + "\n".join(f"- {p}" for p in problems))
        self.problems = problems


def import_responses(context: EvalContext, responses_path: Path) -> ImportResult:
    rubric = load_review_rubric(context)
    document = load_yaml(responses_path)
    problems: list[str] = []

    pack_id = document.get("pack_id")
    key_path = keys_dir(context) / f"{pack_id}.yml" if isinstance(pack_id, str) and pack_id else None
    if key_path is None or not key_path.is_file():
        raise ReviewImportError([f"unknown pack_id {pack_id!r}; no export key found for this eval"])
    key = load_yaml(key_path)
    if key.get("mode") not in PACK_MODES:
        raise ReviewImportError([f"pack mode {key.get('mode')!r} is not supported"])
    if key.get("rubric", {}).get("sha256") != rubric.sha256:
        raise ReviewImportError(["the rubric changed after this pack was exported; export a new pack"])
    reviewer = document.get("reviewer")
    if not isinstance(reviewer, str) or not REVIEWER_PATTERN.fullmatch(reviewer):
        problems.append("reviewer must be an ID using letters, digits, '.', '_' and '-'")
    responses = document.get("responses")
    if not isinstance(responses, list):
        raise ReviewImportError(["responses must be a list"])

    submitted_at = datetime.now(UTC)
    stamp = submitted_at.strftime("%Y%m%dT%H%M%S%fZ")
    seen: set[str] = set()
    planned: list[tuple[Path, dict[str, Any]]] = []
    skipped_blank = 0
    for index, response in enumerate(responses):
        where = f"responses[{index}]"
        if not isinstance(response, dict):
            problems.append(f"{where} must be a mapping")
            continue
        presentation_id = response.get("presentation_id")
        entry = key["items"].get(presentation_id) if isinstance(presentation_id, str) else None
        if entry is None:
            problems.append(f"{where}: unknown presentation_id {presentation_id!r}")
            continue
        if presentation_id in seen:
            problems.append(f"{where}: presentation_id {presentation_id} appears more than once")
            continue
        seen.add(presentation_id)
        dimensions = response.get("dimensions")
        if not isinstance(dimensions, dict):
            problems.append(f"{where}.dimensions must be a mapping")
            continue
        unknown = sorted(set(dimensions) - set(rubric.dimension_ids))
        if unknown:
            problems.append(f"{where}: unknown dimensions {', '.join(map(str, unknown))}")
        if all(isinstance(dimensions.get(d), dict) and dimensions[d].get("pass") is None for d in rubric.dimension_ids):
            skipped_blank += 1  # an untouched template row means "not reviewed yet"
            continue
        recorded: dict[str, Any] = {}
        for dimension in rubric.dimensions:
            outcome = dimensions.get(dimension.id)
            location = f"{where}.dimensions.{dimension.id}"
            if not isinstance(outcome, dict) or not isinstance(outcome.get("pass"), bool):
                problems.append(f"{location}: pass must be true or false")
                continue
            code = outcome.get("reason_code")
            if outcome["pass"]:
                if code not in (None, NO_REASON):
                    problems.append(f"{location}: a passing dimension takes no reason_code")
                code = NO_REASON
            elif code is None:
                problems.append(
                    f"{location}: marked fail but no reason was chosen "
                    f"(pick one of: {', '.join(dimension.reason_codes)})"
                )
                continue
            elif code not in dimension.reason_codes:
                problems.append(
                    f"{location}: reason_code {code!r} is not one of {', '.join(dimension.reason_codes)}"
                )
                continue
            recorded[dimension.id] = {"pass": outcome["pass"], "reason_code": code}
        if len(recorded) != len(rubric.dimensions):
            continue
        run_dir = context.eval_dir / "runs" / entry["run_id"]
        item_dir = run_dir / "items" / entry["item_id"]
        result_path = item_dir / "result.yml"
        current_sha = None
        if result_path.is_file():
            output = load_yaml(result_path).get("output")
            if isinstance(output, dict):
                image_path = item_dir / output["path"]
                current_sha = digest_file(image_path) if image_path.is_file() else None
        if current_sha != entry["image_sha256"]:
            problems.append(f"{where}: the stored image changed since export ({presentation_id}); export a new pack")
            continue
        notes = response.get("notes", "")
        used_other = sorted(d for d, o in recorded.items() if o["reason_code"] == OTHER_REASON)
        if used_other and not (isinstance(notes, str) and notes.strip()):
            problems.append(
                f"{where}: reason_code 'other' on {', '.join(used_other)} needs an explanation in notes"
            )
            continue
        record = {
            "schema_version": 1,
            "review_id": f"{reviewer}--{stamp}",
            "run_id": entry["run_id"],
            "item_id": entry["item_id"],
            "image_sha256": entry["image_sha256"],
            "pack_id": pack_id,
            "reviewer": reviewer,
            "submitted_at": submitted_at.isoformat().replace("+00:00", "Z"),
            "rubric": {"name": rubric.name, "sha256": rubric.sha256},
            "dimensions": recorded,
            "verdict": "pass" if overall_pass(recorded, rubric) else "fail",
            "notes": notes if isinstance(notes, str) else "",
        }
        planned.append((reviews_dir(item_dir) / f"{reviewer}--{stamp}.yml", record))
    if problems:
        raise ReviewImportError(problems)

    result = ImportResult(pack_id, str(reviewer), skipped_blank=skipped_blank)
    try:
        for path, record in planned:
            write_yaml_exclusive(path, record)
            result.written.append(path)
    except BaseException:
        for path in result.written:  # all-or-nothing: undo what this import created
            path.unlink(missing_ok=True)
        raise
    from .run_report import refresh_reports

    refresh_reports(context, [path.parents[3].name for path in result.written])
    return result


# -- status ------------------------------------------------------------------------------


def review_status(context: EvalContext, run_id: str | None = None) -> dict[str, Any]:
    rubric = load_review_rubric(context)
    acceptance = resolved_acceptance(context.evaluation["evaluation"])
    required = acceptance.required_reviews_per_item
    selection = select_generated_items(context, run_id)
    items, awaiting, stale_records = [], 0, 0
    for entry in selection.items:
        current, stale = split_reviews(
            load_reviews(entry.item_dir), image_sha256=entry.image_sha256, rubric_sha256=rubric.sha256
        )
        stale_records += len(stale)
        state = human_state(current, acceptance)
        owed = state["owed"]
        awaiting += 1 if owed else 0
        items.append(
            {
                "run_id": entry.run_id,
                "item_id": entry.item_id,
                "reviews": len(current),
                "owed": owed,
                "state": state["state"],
                "stale_records": len(stale),
            }
        )
    return {
        "required_reviews_per_item": required,
        "items": items,
        "awaiting_review": awaiting,
        "reviewed": len(items) - awaiting,
        "stale_records": stale_records,
        "skipped": selection.skipped,
    }
