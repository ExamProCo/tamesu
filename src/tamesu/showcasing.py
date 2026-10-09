from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .artifacts import write_json, write_text, write_yaml
from .config import ID_PATTERN
from .errors import TamesuError
from .packaging import (
    SEMVER,
    _read_archive,
    _sha256_file,
    _write_archive,
    _yaml_bytes,
    build_inventory,
    inventory_digest,
    scan_secrets,
    verify_archive,
)
from .presenting import present_case


SITE_CSS = "body{margin:0;background:#f6f2ea;color:#25211d;font:16px/1.5 system-ui,sans-serif}main{max-width:1100px;margin:auto;padding:3rem 1.25rem}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;margin:.2em 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:1rem}.card{display:block;color:inherit;text-decoration:none;background:#fff;border:1px solid #d9d0c3;border-radius:12px;padding:1.2rem}.card:hover{border-color:#8b5d35}.meta{color:#6d645b}.tag{display:inline-block;border:1px solid #d9d0c3;border-radius:999px;padding:.1rem .5rem;margin:.1rem}input{width:100%;font:inherit;padding:.8rem;border:1px solid #bdb2a4;border-radius:8px;margin:1rem 0 2rem}@media(prefers-color-scheme:dark){body{background:#171512;color:#f4eee5}.card{background:#211e1a;border-color:#494139}.meta{color:#b9afa2}.tag{border-color:#494139}}"
SITE_JS = "const q=document.querySelector('#search');if(q){q.addEventListener('input',()=>{const v=q.value.toLowerCase();document.querySelectorAll('[data-search]').forEach(c=>c.hidden=!c.dataset.search.includes(v));});}"


def publish_archive(archive: Path, registry_root: Path) -> Path:
    verified = verify_archive(archive)
    publication = verified.publication
    publisher = publication["publisher"]
    name = publication["name"]
    version = publication["version"]
    destination = registry_root.resolve() / publisher / name / version
    if destination.exists():
        raise TamesuError(f"Registry version already exists: {publisher}/{name}/{version}")
    _check_version_increase(destination.parent, version)
    _, files = _read_archive(verified.archive)
    payload = {path.removeprefix("payload/"): data for path, data in files.items() if path.startswith("payload/")}
    scan_secrets({"publication.yml": _yaml_bytes(publication), **payload})
    temporary_parent = destination.parent
    temporary_parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=temporary_parent))
    try:
        for relative, data in sorted(payload.items()):
            target = temporary / "payload" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        manifest = dict(verified.manifest)
        registry_archive = temporary / "package.tamesu.tar.gz"
        entries = {"package.yml": _yaml_bytes(manifest), **{f"payload/{path}": data for path, data in payload.items()}}
        _write_archive(registry_archive, entries)
        archive_sha256 = f"sha256:{_sha256_file(registry_archive)}"
        entry = {
            "schema_version": 1,
            "publication": publication,
            "content_digest": verified.content_digest,
            "archive_sha256": archive_sha256,
            "tamesu_version": manifest.get("tamesu_version", __version__),
            "inventory": manifest["inventory"],
        }
        write_yaml(temporary / "entry.yml", entry)
        registry_archive.unlink()
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def validate_registry(registry_root: Path, *, author: str | None = None) -> list[dict[str, Any]]:
    registry_root = registry_root.resolve()
    publishers = _load_publishers(registry_root)
    entries: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    for entry_path in sorted(registry_root.glob("*/*/*/entry.yml")):
        entry = yaml.safe_load(entry_path.read_text(encoding="utf-8"))
        if (
            not isinstance(entry, dict)
            or set(entry) != {
                "schema_version", "publication", "content_digest",
                "archive_sha256", "tamesu_version", "inventory",
            }
            or entry.get("schema_version") != 1
        ):
            raise TamesuError(f"Invalid registry entry: {entry_path}")
        publication = entry.get("publication")
        if not isinstance(publication, dict):
            raise TamesuError(f"Registry entry has no publication metadata: {entry_path}")
        publisher, name, version = entry_path.parents[2].name, entry_path.parents[1].name, entry_path.parent.name
        identity = (publisher, name, version)
        if identity in identities:
            raise TamesuError(f"Duplicate registry identity: {'/'.join(identity)}")
        identities.add(identity)
        if (publication.get("publisher"), publication.get("name"), publication.get("version")) != identity:
            raise TamesuError(f"Registry path and publication metadata disagree: {entry_path}")
        if not SEMVER.fullmatch(version):
            raise TamesuError(f"Invalid registry version: {version}")
        if publication.get("profile") in {"full", "rescorable"}:
            review = publication.get("data_review")
            if not isinstance(review, dict) or review.get("acknowledged") is not True:
                raise TamesuError(f"Data review is not acknowledged: {entry_path}")
        payload = _payload_bytes(entry_path.parent / "payload")
        scan_secrets({"publication.yml": _yaml_bytes(publication), **payload})
        inventory = build_inventory(payload)
        digest = inventory_digest(inventory)
        if entry.get("inventory") != inventory or entry.get("content_digest") != digest:
            raise TamesuError(f"Registry payload does not match its inventory: {entry_path}")
        expected_archive = _registry_archive_bytes(entry, payload)
        actual_sha = f"sha256:{hashlib.sha256(expected_archive).hexdigest()}"
        if entry.get("archive_sha256") != actual_sha:
            raise TamesuError(f"Registry archive_sha256 does not reproduce: {entry_path}")
        with tempfile.TemporaryDirectory() as check_directory:
            check_archive = Path(check_directory) / "registry-entry.tamesu.tar.gz"
            check_archive.write_bytes(expected_archive)
            verified = verify_archive(check_archive)
            if verified.content_digest != digest:
                raise TamesuError(f"Registry package verification failed: {entry_path}")
        publisher_record = publishers.get(publisher)
        if publisher_record is None:
            raise TamesuError(f"Publisher is not registered: {publisher}")
        if author is not None:
            accounts = publisher_record["github_accounts"]
            if author not in accounts:
                raise TamesuError(f"GitHub account {author!r} cannot publish under {publisher!r}")
        entries.append({**entry, "path": entry_path.parent, "identity": identity})
    _validate_monotonic_versions(entries)
    return entries


def validate_registry_changes(
    registry_root: Path, *, author: str, changed_files: list[str]
) -> list[dict[str, Any]]:
    """Validate a PR-sized registry change, including ownership and immutability."""
    entries = validate_registry(registry_root)
    registry_root = registry_root.resolve()
    package_changes: dict[tuple[str, str, str], set[str]] = {}
    publisher_records_changed: set[str] = set()
    for raw in changed_files:
        status, separator, raw_path = raw.partition(":")
        if not separator or status not in {"A", "M", "D", "R"}:
            raise TamesuError(
                f"Changed file must use A:, M:, D:, or R: status: {raw!r}"
            )
        path = Path(raw_path)
        absolute = path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
        try:
            relative = absolute.relative_to(registry_root)
        except ValueError:
            continue
        parts = relative.parts
        if len(parts) == 2 and parts[0] == "publishers" and parts[1].endswith(".yml"):
            publisher_records_changed.add(Path(parts[1]).stem)
            continue
        if len(parts) < 4:
            continue
        identity = (parts[0], parts[1], parts[2])
        package_changes.setdefault(identity, set()).add(status)
    records_by_identity = {record["identity"]: record for record in entries}
    for identity, statuses in package_changes.items():
        publisher, name, version = identity
        if "D" in statuses or "M" in statuses or "R" in statuses:
            raise TamesuError(
                f"Published registry versions are immutable: {publisher}/{name}/{version}"
            )
        if identity not in records_by_identity:
            raise TamesuError(f"Added registry version has no valid entry.yml: {'/'.join(identity)}")
        if publisher in publisher_records_changed:
            raise TamesuError(
                f"Publisher {publisher!r} must be registered in a separate reviewed change"
            )
        publisher_path = registry_root / "publishers" / f"{publisher}.yml"
        if not publisher_path.is_file():
            raise TamesuError(f"Publisher is not registered: {publisher}")
        publisher_record = yaml.safe_load(publisher_path.read_text(encoding="utf-8"))
        accounts = publisher_record.get("github_accounts", []) if isinstance(publisher_record, dict) else []
        if author not in accounts:
            raise TamesuError(f"GitHub account {author!r} cannot publish under {publisher!r}")
        older = [
            candidate["identity"][2]
            for candidate in entries
            if candidate["identity"][:2] == (publisher, name)
            and candidate["identity"] != identity
        ]
        if older and _semver_key(version) <= max(_semver_key(value) for value in older):
            raise TamesuError(
                f"Version {version} must be greater than existing version "
                f"{max(older, key=_semver_key)}"
            )
    return entries


def build_site(registry_root: Path, output_dir: Path | None = None) -> Path:
    registry_root = registry_root.resolve()
    entries = validate_registry(registry_root)
    publisher_records = _load_publishers(registry_root)
    destination = (output_dir or registry_root.parent / "build" / "site").resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (temporary / "assets").mkdir()
        (temporary / "downloads").mkdir()
        write_text(temporary / "assets" / "site.css", SITE_CSS)
        write_text(temporary / "assets" / "site.js", SITE_JS)
        index: list[dict[str, Any]] = []
        publishers: dict[str, list[dict[str, Any]]] = {}
        for record in entries:
            publication = record["publication"]
            publisher, name, version = record["identity"]
            stage_root = Path(tempfile.mkdtemp(prefix="tamesu-site-case-"))
            try:
                stage_case = stage_root / "cases" / name
                shutil.copytree(record["path"] / "payload", stage_case)
                write_yaml(stage_case / "publication.yml", publication)
                page_dir = temporary / publisher / name / version
                present_case(stage_case, page_dir, include_text_artifacts=False)  # no logs or source on a public site
            finally:
                shutil.rmtree(stage_root, ignore_errors=True)
            payload = _payload_bytes(record["path"] / "payload")
            archive_name = f"{publisher}-{name}-{version}.tamesu.tar.gz"
            manifest = {
                "schema_version": 1,
                "content_digest": record["content_digest"],
                "profile": publication["profile"],
                "tamesu_version": record.get("tamesu_version", __version__),
                "inventory": record["inventory"],
                "publication": publication,
            }
            _write_archive(temporary / "downloads" / archive_name, {"package.yml": _yaml_bytes(manifest), **{f"payload/{path}": data for path, data in payload.items()}})
            parent = publication.get("forked_from")
            parent_record = _find_parent_record(parent, entries)
            children = [child for child in entries if isinstance(child["publication"].get("forked_from"), dict) and child["publication"]["forked_from"].get("content_digest") == record["content_digest"]]
            comparison = parent_record or _previous_version(record, entries)
            if comparison:
                write_text(page_dir / "compare.html", _compare_html(comparison, record))
            _append_showcase_links(page_dir / "index.html", archive_name, record, parent_record, parent, children, comparison is not None)
            models = _registry_models(payload)
            card = {
                "publisher": publisher, "name": name, "version": version,
                "title": publication["title"], "summary": publication["summary"],
                "tags": publication.get("tags", []), "profile": publication["profile"],
                "models": models,
                "content_digest": record["content_digest"], "archive_sha256": record["archive_sha256"],
                "url": f"{publisher}/{name}/{version}/index.html",
            }
            index.append(card)
            publishers.setdefault(publisher, []).append(card)
        index.sort(key=lambda row: (row["publisher"], row["name"], _semver_key(row["version"])), reverse=True)
        write_json(temporary / "index.json", index)
        write_text(temporary / "index.html", _home_html(index))
        for publisher, cards in publishers.items():
            page = temporary / "publishers" / publisher / "index.html"
            page.parent.mkdir(parents=True, exist_ok=True)
            display_name = publisher_records.get(publisher, {}).get(
                "display_name", publisher
            )
            write_text(page, _publisher_html(publisher, str(display_name), cards))
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def _append_showcase_links(index_path: Path, archive_name: str, record: dict[str, Any], parent_record: dict[str, Any] | None, parent: Any, children: list[dict[str, Any]], has_comparison: bool) -> None:
    source = index_path.read_text(encoding="utf-8")
    links = [f'<p><a href="../../../downloads/{_e(archive_name)}">Download package</a> · archive <code>{_e(record["archive_sha256"])}</code></p>']
    if parent_record:
        publisher, name, version = parent_record["identity"]
        links.append(f'<p>Parent: <a href="../../../{_e(publisher)}/{_e(name)}/{_e(version)}/index.html">{_e(publisher)}/{_e(name)} {_e(version)}</a> · <code>{_e(parent_record["content_digest"])}</code></p>')
    elif isinstance(parent, dict):
        links.append(f'<p>Parent (not in this registry): {_e(parent.get("publisher"))}/{_e(parent.get("name"))} {_e(parent.get("version"))} · <code>{_e(parent.get("content_digest"))}</code></p>')
    if children:
        links.append("<p>Forks: " + ", ".join(f'<a href="../../../{_e(child["identity"][0])}/{_e(child["identity"][1])}/{_e(child["identity"][2])}/index.html">{_e(child["identity"][0])}/{_e(child["identity"][1])} {_e(child["identity"][2])}</a>' for child in children) + "</p>")
    if has_comparison:
        links.append('<p><a href="compare.html">Compare with parent or previous version</a></p>')
    source = source.replace("</header>", "".join(links) + "</header>", 1)
    write_text(index_path, source)


def _home_html(index: list[dict[str, Any]]) -> str:
    cards = "".join(_card(card, prefix="") for card in index)
    return f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'self\'; script-src \'self\'; base-uri \'none\'"><title>Tamesu Showcase</title><link rel="stylesheet" href="assets/site.css"><script src="assets/site.js" defer></script></head><body><main><p class="meta">Tamesu Showcase</p><h1>Evidence worth sharing.</h1><p>Browse reproducible AI evaluation case studies.</p><label for="search">Search cases and tags</label><input id="search" type="search" placeholder="Search"><div class="grid">{cards or "<p>No packages published yet.</p>"}</div></main></body></html>\n'


def _publisher_html(
    publisher: str, display_name: str, cards: list[dict[str, Any]]
) -> str:
    content = "".join(_card(card, prefix="../../") for card in cards)
    return f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(display_name)}</title><link rel="stylesheet" href="../../assets/site.css"></head><body><main><p><a href="../../index.html">← Showcase</a></p><p class="meta">{_e(publisher)}</p><h1>{_e(display_name)}</h1><div class="grid">{content}</div></main></body></html>\n'


def _card(card: dict[str, Any], *, prefix: str) -> str:
    search = " ".join([card["title"], card["summary"], card["publisher"], *card["tags"], *card.get("models", [])]).lower()
    tags = "".join(f'<span class="tag">{_e(tag)}</span>' for tag in card["tags"])
    models = ", ".join(card.get("models", []))
    return f'<a class="card" data-search="{_e(search)}" href="{prefix}{_e(card["url"])}"><p class="meta">{_e(card["publisher"])} · v{_e(card["version"])} · {_e(card["profile"])}</p><h2>{_e(card["title"])}</h2><p>{_e(card["summary"])}</p><p class="meta">{_e(models)}</p><p>{tags}</p></a>'


def _find_parent_record(parent: Any, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(parent, dict):
        return None
    identity = (parent.get("publisher"), parent.get("name"), parent.get("version"))
    exact = next((record for record in entries if record["identity"] == identity and record["content_digest"] == parent.get("content_digest")), None)
    if exact:
        return exact
    return next((record for record in entries if record["content_digest"] == parent.get("content_digest")), None)


def _previous_version(record: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    publisher, name, version = record["identity"]
    candidates = [candidate for candidate in entries if candidate["identity"][:2] == (publisher, name) and _semver_key(candidate["identity"][2]) < _semver_key(version)]
    return max(candidates, key=lambda candidate: _semver_key(candidate["identity"][2]), default=None)


def _compare_html(before: dict[str, Any], after: dict[str, Any]) -> str:
    before_inventory = {row["path"]: row["sha256"] for row in before["inventory"]}
    after_inventory = {row["path"]: row["sha256"] for row in after["inventory"]}
    added = sorted(set(after_inventory) - set(before_inventory))
    removed = sorted(set(before_inventory) - set(after_inventory))
    changed = sorted(path for path in set(before_inventory) & set(after_inventory) if before_inventory[path] != after_inventory[path])
    method_changes = [path for path in [*added, *removed, *changed] if "/prompts/" in f"/{path}" or "/datasets/" in f"/{path}" or "/schemas/" in f"/{path}"]
    before_metrics = _registry_metrics(_payload_bytes(before["path"] / "payload"))
    after_metrics = _registry_metrics(_payload_bytes(after["path"] / "payload"))
    metric_keys = sorted(set(before_metrics) | set(after_metrics))
    metric_rows = "".join(f"<tr><td>{_e(key)}</td><td>{_score(before_metrics.get(key))}</td><td>{_score(after_metrics.get(key))}</td></tr>" for key in metric_keys) or '<tr><td colspan="3">No comparable run metrics are included.</td></tr>'
    before_label = "/".join(before["identity"])
    after_label = "/".join(after["identity"])
    return f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'self\'; base-uri \'none\'"><title>Compare {_e(after_label)}</title><link rel="stylesheet" href="assets/style.css"></head><body><main><p><a href="index.html">← Case study</a></p><h1>Compare versions</h1><p><code>{_e(before_label)}</code> → <code>{_e(after_label)}</code></p><h2>Payload changes</h2><p>{len(added)} added, {len(changed)} changed, {len(removed)} removed.</p><h3>Prompts, datasets, and schemas</h3>{_path_list(method_changes)}<h3>All added</h3>{_path_list(added)}<h3>All changed</h3>{_path_list(changed)}<h3>All removed</h3>{_path_list(removed)}<h2>Leaderboard movement</h2><table><thead><tr><th>Eval · metric · model / arm</th><th>Before</th><th>After</th></tr></thead><tbody>{metric_rows}</tbody></table><p class="notice">This view reports stored score movement and file changes. It does not attribute causation.</p></main></body></html>\n'


def _path_list(paths: list[str]) -> str:
    return "<ul>" + "".join(f"<li><code>{_e(path)}</code></li>" for path in paths) + "</ul>" if paths else "<p>None.</p>"


def _registry_models(payload: dict[str, bytes]) -> list[str]:
    models: set[str] = set()
    for path, data in payload.items():
        if Path(path).name != "eval.yml":
            continue
        value = yaml.safe_load(data)
        if isinstance(value, dict):
            for run in value.get("runs", []):
                if isinstance(run, dict) and isinstance(run.get("model"), str):
                    models.add(run["model"])
    return sorted(models)


def _registry_metrics(payload: dict[str, bytes]) -> dict[str, float]:
    metrics: dict[str, list[float]] = {}
    for path, data in payload.items():
        if Path(path).name != "report.yml" or "/runs/" not in f"/{path}":
            continue
        report = yaml.safe_load(data)
        run_path = str(Path(path).parent / "run.yml")
        run = yaml.safe_load(payload[run_path]) if run_path in payload else {}
        if not isinstance(report, dict) or not isinstance(run, dict):
            continue
        eval_prefix = path.split("/runs/", 1)[0]
        eval_path = f"{eval_prefix}/eval.yml"
        evaluation = yaml.safe_load(payload[eval_path]) if eval_path in payload else {}
        primary = evaluation.get("metrics", {}).get("primary") if isinstance(evaluation, dict) else None
        value = report.get("metrics", {}).get(primary) if primary else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            label = f"{Path(eval_prefix).name} · {primary} · {run.get('model', 'unknown')} / {run.get('arm_id', 'unknown')}"
            metrics.setdefault(label, []).append(float(value))
    return {label: sum(values) / len(values) for label, values in metrics.items()}


def _score(value: Any) -> str:
    return "—" if not isinstance(value, (int, float)) or isinstance(value, bool) else f"{value:.4f}"


def _registry_archive_bytes(entry: dict[str, Any], payload: dict[str, bytes]) -> bytes:
    publication = entry["publication"]
    manifest = {
        "schema_version": 1, "content_digest": entry["content_digest"],
        "profile": publication["profile"], "tamesu_version": entry.get("tamesu_version", __version__),
        "inventory": entry["inventory"], "publication": publication,
    }
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "package.tar.gz"
        _write_archive(archive, {"package.yml": _yaml_bytes(manifest), **{f"payload/{path}": data for path, data in payload.items()}})
        return archive.read_bytes()


def _payload_bytes(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        raise TamesuError(f"Registry payload directory is missing: {root}")
    payload: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TamesuError(f"Registry payload contains a symlink: {path}")
        if path.is_file():
            payload[path.relative_to(root).as_posix()] = path.read_bytes()
    return payload


def _load_publishers(registry_root: Path) -> dict[str, dict[str, Any]]:
    publishers: dict[str, dict[str, Any]] = {}
    for path in sorted((registry_root / "publishers").glob("*.yml")):
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "slug", "display_name", "github_accounts"
        }:
            raise TamesuError(f"Invalid publisher record: {path}")
        slug = value.get("slug")
        accounts = value.get("github_accounts")
        if (
            value.get("schema_version") != 1
            or slug != path.stem
            or not isinstance(slug, str)
            or not ID_PATTERN.fullmatch(slug)
        ):
            raise TamesuError(f"Publisher record path and slug disagree: {path}")
        if not isinstance(value.get("display_name"), str) or not value["display_name"].strip():
            raise TamesuError(f"Publisher display_name is required: {path}")
        if not isinstance(accounts, list) or not accounts or any(
            not isinstance(account, str) or not account.strip() for account in accounts
        ):
            raise TamesuError(f"Publisher github_accounts are invalid: {path}")
        if len(accounts) != len(set(accounts)):
            raise TamesuError(f"Publisher github_accounts contain duplicates: {path}")
        publishers[slug] = value
    return publishers


def _check_version_increase(package_root: Path, version: str) -> None:
    existing = [path.name for path in package_root.iterdir() if path.is_dir() and SEMVER.fullmatch(path.name)] if package_root.is_dir() else []
    if existing and _semver_key(version) <= max(_semver_key(value) for value in existing):
        raise TamesuError(f"Version {version} must be greater than existing version {max(existing, key=_semver_key)}")


def _validate_monotonic_versions(entries: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str], list[str]] = {}
    for record in entries:
        publisher, name, version = record["identity"]
        grouped.setdefault((publisher, name), []).append(version)
    for identity, versions in grouped.items():
        if len(versions) != len(set(versions)):
            raise TamesuError(f"Duplicate versions for {identity[0]}/{identity[1]}")


def _semver_key(version: str) -> tuple[int, int, int, int, str]:
    match = SEMVER.fullmatch(version)
    if not match:
        return (-1, -1, -1, -1, version)
    core = version.split("+", 1)[0]
    main, separator, prerelease = core.partition("-")
    major, minor, patch = (int(value) for value in main.split("."))
    return major, minor, patch, 0 if separator else 1, prerelease


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)
