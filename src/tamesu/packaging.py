from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

from . import __version__
from .artifacts import write_yaml
from .config import ID_PATTERN, load_yaml
from .errors import TamesuError


MAX_FILES = 10_000
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_EXPANDED_SIZE = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
PROFILES = {"full", "rescorable", "report-only"}
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

# Deliberately conservative: a hit stops publication and asks the author to inspect it.
SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*[:=]\s*(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk|pk)-(?:live|test)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:sk-ant|sk-proj|AIza|AKIA|ASIA)[A-Za-z0-9_/-]{12,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}"),
    re.compile(r"(?i)https?://[^\s]+[?&](?:x-amz-signature|sig|signature|token)=[^&\s]+"),
)


@dataclass(frozen=True)
class VerifiedPackage:
    archive: Path
    manifest: dict[str, Any]
    publication: dict[str, Any]
    content_digest: str
    archive_sha256: str


def resolve_case(project_root: Path, value: str | Path) -> Path:
    raw = Path(value)
    candidates = [raw]
    if not raw.is_absolute():
        candidates.extend((Path.cwd() / raw, project_root / raw, project_root / "cases" / raw))
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.name == "case.yml" and resolved.is_file():
            return resolved.parent
        if (resolved / "case.yml").is_file():
            return resolved
    raise TamesuError(f"Case not found: {value}")


def load_publication(case_dir: Path, *, required: bool = True) -> dict[str, Any] | None:
    path = case_dir / "publication.yml"
    if not path.is_file():
        if required:
            raise TamesuError(f"Missing publication metadata: {path}")
        return None
    publication = load_yaml(path)
    errors: list[str] = []
    allowed = {
        "schema_version", "name", "publisher", "version", "title", "summary",
        "authors", "license", "tags", "profile", "data_review", "forked_from",
    }
    for key in sorted(set(publication) - allowed):
        errors.append(f"publication.{key} is not a recognized field")
    if publication.get("schema_version") != 1:
        errors.append("publication.schema_version must be 1")
    for key in ("name", "publisher"):
        value = publication.get(key)
        if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
            errors.append(f"publication.{key} must use lowercase kebab-case")
    version = publication.get("version")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        errors.append("publication.version must be semantic version text such as 1.2.0")
    for key in ("title", "summary", "license"):
        if not isinstance(publication.get(key), str) or not publication[key].strip():
            errors.append(f"publication.{key} must be a non-empty string")
    authors = publication.get("authors")
    if not isinstance(authors, list) or not authors:
        errors.append("publication.authors must contain at least one author")
    elif any(not isinstance(author, dict) or not isinstance(author.get("name"), str) or not author["name"].strip() for author in authors):
        errors.append("each publication author must have a non-empty name")
    elif any(set(author) != {"name"} for author in authors):
        errors.append("publication authors may contain only the name field")
    tags = publication.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not ID_PATTERN.fullmatch(tag) for tag in tags):
        errors.append("publication.tags must be a list of lowercase kebab-case strings")
    elif len(tags) != len(set(tags)):
        errors.append("publication.tags must not contain duplicates")
    profile = publication.get("profile")
    if profile not in PROFILES:
        errors.append(f"publication.profile must be one of: {', '.join(sorted(PROFILES))}")
    if profile in {"full", "rescorable"}:
        review = publication.get("data_review")
        if not isinstance(review, dict) or review.get("acknowledged") is not True:
            errors.append("publication.data_review.acknowledged must be true for this profile")
        elif not isinstance(review.get("note"), str) or not review["note"].strip():
            errors.append("publication.data_review.note must explain the review")
        elif set(review) != {"acknowledged", "note"}:
            errors.append("publication.data_review has an unrecognized field")
    parent = publication.get("forked_from")
    if parent is not None:
        if not isinstance(parent, dict):
            errors.append("publication.forked_from must be a mapping")
        else:
            if set(parent) != {"publisher", "name", "version", "content_digest"}:
                errors.append("publication.forked_from has an unrecognized or missing field")
            for key in ("publisher", "name"):
                if not isinstance(parent.get(key), str) or not ID_PATTERN.fullmatch(parent[key]):
                    errors.append(f"publication.forked_from.{key} must use lowercase kebab-case")
            if not isinstance(parent.get("version"), str) or not SEMVER.fullmatch(parent["version"]):
                errors.append("publication.forked_from.version must be semantic version text")
            if not _valid_digest(parent.get("content_digest")):
                errors.append("publication.forked_from.content_digest must be a sha256 digest")
    case_name = load_yaml(case_dir / "case.yml").get("name")
    if publication.get("name") != case_name:
        errors.append("publication.name must match case.name")
    if errors:
        raise TamesuError("Invalid publication.yml:\n" + "\n".join(f"- {error}" for error in errors))
    return publication


def validate_portable(case_dir: Path) -> None:
    case_dir = case_dir.resolve()
    failures: list[str] = []
    for eval_path in sorted(case_dir.glob("experiments/*/evals/*/eval.yml")):
        evaluation = load_yaml(eval_path)
        dataset_path = _portable_reference(eval_path.parent, evaluation.get("dataset"), case_dir, "eval.dataset", failures)
        _portable_reference(eval_path.parent, evaluation.get("output_schema"), case_dir, "eval.output_schema", failures)
        for index, arm in enumerate(evaluation.get("arms", [])):
            if not isinstance(arm, dict):
                continue
            for role, raw in (arm.get("prompts") or {}).items():
                _portable_reference(eval_path.parent, raw, case_dir, f"eval.arms[{index}].prompts.{role}", failures)
            for index2, raw in enumerate(arm.get("references", [])):
                _portable_reference(eval_path.parent, raw, case_dir, f"eval.arms[{index}].references[{index2}]", failures)
        if dataset_path and dataset_path.is_file():
            dataset = load_yaml(dataset_path)
            _validate_asset_references(dataset.get("shared_assets"), dataset_path.parent, case_dir, "dataset.shared_assets", failures)
            for index, item in enumerate(dataset.get("items", [])):
                if isinstance(item, dict):
                    _validate_asset_references(item.get("assets"), dataset_path.parent, case_dir, f"dataset.items[{index}].assets", failures)
    if failures:
        raise TamesuError("Case is not portable:\n" + "\n".join(f"- {failure}" for failure in failures))


def pack_case(case_dir: Path, output_dir: Path | None = None, *, presentation_dir: Path | None = None) -> Path:
    case_dir = case_dir.resolve()
    publication = load_publication(case_dir)
    assert publication is not None
    validate_portable(case_dir)
    payload = collect_payload(case_dir, publication["profile"])
    scan_secrets({"publication.yml": _yaml_bytes(publication), **payload})
    inventory = build_inventory(payload)
    content_digest = inventory_digest(inventory)
    manifest = {
        "schema_version": 1,
        "content_digest": content_digest,
        "profile": publication["profile"],
        "tamesu_version": __version__,
        "inventory": inventory,
        "publication": publication,
    }
    manifest_bytes = _yaml_bytes(manifest)
    entries: dict[str, bytes] = {"package.yml": manifest_bytes}
    entries.update({f"payload/{path}": data for path, data in payload.items()})
    if presentation_dir and presentation_dir.is_dir():
        for path in _regular_files(presentation_dir):
            entries[f"presentation/{path.relative_to(presentation_dir).as_posix()}"] = path.read_bytes()
    destination = (output_dir or case_dir.parent.parent / "build" / "packages").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    filename = f"{publication['publisher']}-{publication['name']}-{publication['version']}.tamesu.tar.gz"
    archive = destination / filename
    _write_archive(archive, entries)
    return archive


def collect_payload(case_dir: Path, profile: str) -> dict[str, bytes]:
    if profile not in PROFILES:
        raise TamesuError(f"Unknown package profile: {profile}")
    payload: dict[str, bytes] = {}
    for path in _regular_files(case_dir):
        relative = path.relative_to(case_dir).as_posix()
        parts = PurePosixPath(relative).parts
        if any(part.startswith(".") for part in parts) or relative in {
            "package-view.yml",
            "publication.yml",
        }:
            continue
        if profile == "rescorable" and ("logs" in parts or relative.endswith(".jsonl")):
            continue
        if profile == "report-only" and not _report_only_path(parts):
            continue
        data = path.read_bytes()
        if profile == "full" and "logs" in parts and relative.endswith(".jsonl"):
            data = _scrub_log(data)
        payload[relative] = data
    if profile == "report-only":
        payload["package-view.yml"] = _yaml_bytes(
            {
                "schema_version": 1,
                "view_only": True,
                "withheld": ["datasets", "run outputs", "logs"],
                "reason": "This report-only package does not contain data needed to run or rescore.",
            }
        )
    return dict(sorted(payload.items()))


def build_inventory(payload: dict[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        for path, data in sorted(payload.items())
    ]


def canonical_inventory(inventory: Iterable[dict[str, Any]]) -> bytes:
    rows = sorted(inventory, key=lambda row: str(row["path"]).encode("utf-8"))
    return "".join(f"{row['sha256']}  {row['path']}\n" for row in rows).encode("utf-8")


def inventory_digest(inventory: Iterable[dict[str, Any]]) -> str:
    return f"sha256:{hashlib.sha256(canonical_inventory(inventory)).hexdigest()}"


def verify_archive(archive: Path) -> VerifiedPackage:
    archive = archive.resolve()
    try:
        raw_size = archive.stat().st_size
    except OSError as exc:
        raise TamesuError(f"Package archive is not readable: {archive}") from exc
    members, files = _read_archive(archive)
    for member in members:
        name = member.name.rstrip("/")
        top = PurePosixPath(name).parts[0]
        if name == "package.yml":
            if not member.isfile():
                raise TamesuError("package.yml must be a regular file")
        elif top not in {"payload", "presentation"}:
            raise TamesuError(f"Unexpected package entry: {member.name}")
        elif name in {"payload", "presentation"} and not member.isdir():
            raise TamesuError(f"{name} must be a directory")
    if "package.yml" not in files:
        raise TamesuError("Package is missing package.yml")
    try:
        manifest = yaml.safe_load(files["package.yml"])
    except yaml.YAMLError as exc:
        raise TamesuError(f"Invalid package.yml: {exc}") from exc
    _validate_package_manifest(manifest)
    publication = manifest["publication"]
    expected = manifest["inventory"]
    payload = {name.removeprefix("payload/"): data for name, data in files.items() if name.startswith("payload/")}
    actual = build_inventory(payload)
    if actual != expected:
        expected_by_path = {row.get("path"): row for row in expected}
        actual_by_path = {row.get("path"): row for row in actual}
        names = sorted(set(expected_by_path) | set(actual_by_path))
        mismatch = next((name for name in names if expected_by_path.get(name) != actual_by_path.get(name)), "unknown")
        raise TamesuError(f"Payload does not match the inventory: {mismatch}")
    digest = inventory_digest(actual)
    if manifest["content_digest"] != digest:
        raise TamesuError("package.yml content_digest does not match the canonical inventory")
    _validate_payload_manifests(payload, manifest["profile"], publication)
    expanded = sum(len(value) for value in files.values())
    if raw_size and expanded / raw_size > MAX_COMPRESSION_RATIO:
        raise TamesuError(f"Archive compression ratio exceeds {MAX_COMPRESSION_RATIO}:1")
    if manifest["profile"] == "report-only" and "payload/package-view.yml" not in files:
        raise TamesuError("report-only package is missing package-view.yml")
    return VerifiedPackage(
        archive=archive,
        manifest=manifest,
        publication=publication,
        content_digest=digest,
        archive_sha256=f"sha256:{_sha256_file(archive)}",
    )


def unpack_archive(archive: Path, cases_dir: Path, *, case_name: str | None = None) -> Path:
    verified = verify_archive(archive)
    name = case_name or verified.publication["name"]
    if not ID_PATTERN.fullmatch(name):
        raise TamesuError("Unpacked case name must use lowercase kebab-case")
    destination = cases_dir.resolve() / name
    if destination.exists():
        raise TamesuError(f"Refusing to overwrite existing path: {destination}")
    _, files = _read_archive(verified.archive)
    cases_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=cases_dir))
    try:
        for member_name, data in sorted(files.items()):
            if not member_name.startswith("payload/"):
                continue
            relative = PurePosixPath(member_name).relative_to("payload")
            target = temporary.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        write_yaml(temporary / "publication.yml", verified.publication)
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def fork_archive(archive: Path, cases_dir: Path, publisher: str, *, name: str | None = None, version: str = "0.1.0") -> Path:
    verified = verify_archive(archive)
    fork_name = name or verified.publication["name"]
    if not ID_PATTERN.fullmatch(publisher):
        raise TamesuError("Fork publisher must use lowercase kebab-case")
    if not ID_PATTERN.fullmatch(fork_name):
        raise TamesuError("Fork name must use lowercase kebab-case")
    if not SEMVER.fullmatch(version):
        raise TamesuError("Fork version must use semantic version text such as 0.1.0")
    destination = unpack_archive(archive, cases_dir, case_name=fork_name)
    case_path = destination / "case.yml"
    case = load_yaml(case_path)
    if case.get("name") != fork_name:
        case["name"] = fork_name
        write_yaml(case_path, case)
    publication = dict(verified.publication)
    publication.update({"publisher": publisher, "name": fork_name, "version": version})
    publication["forked_from"] = {
        "publisher": verified.publication["publisher"],
        "name": verified.publication["name"],
        "version": verified.publication["version"],
        "content_digest": verified.content_digest,
    }
    write_yaml(destination / "publication.yml", publication)
    return destination


def scan_secrets(files: dict[str, bytes]) -> None:
    for path, data in files.items():
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            raise TamesuError(f"Potential secret found in {path}; remove it before packing")


def _portable_reference(base: Path, raw: Any, case_dir: Path, label: str, failures: list[str]) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    candidate = base / raw
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        failures.append(f"{label} references missing file {raw!r}")
        return None
    try:
        resolved.relative_to(case_dir)
    except ValueError:
        failures.append(f"{label} leaves the case directory: {raw!r} -> {resolved}")
        return None
    if candidate.is_symlink() or not resolved.is_file():
        failures.append(f"{label} must reference a regular file: {raw!r}")
        return None
    return resolved


def _validate_asset_references(value: Any, base: Path, case_dir: Path, label: str, failures: list[str]) -> None:
    if isinstance(value, str):
        _portable_reference(base, value, case_dir, label, failures)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_asset_references(child, base, case_dir, f"{label}[{index}]", failures)
    elif isinstance(value, dict):
        for key, child in value.items():
            _validate_asset_references(child, base, case_dir, f"{label}.{key}", failures)


def _regular_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise TamesuError(f"Symlinks cannot be packaged: {path}")
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix().encode("utf-8"))


def _report_only_path(parts: tuple[str, ...]) -> bool:
    path = PurePosixPath(*parts).as_posix()
    if path in {"case.yml", "README.md"}:
        return True
    if "datasets" in parts or "logs" in parts:
        return False
    if "prompts" in parts or "schemas" in parts:
        return True
    if parts[-1] in {"README.md", "eval.yml", "analysis.md", "evaluation-report.md", "leaderboard.md", "run.yml", "report.yml", "result.yml"}:
        return True
    return False


def _scrub_log(data: bytes) -> bytes:
    lines: list[str] = []
    for raw_line in data.decode("utf-8", errors="replace").splitlines():
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError:
            lines.append(raw_line)
            continue
        lines.append(json.dumps(_scrub_value(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _scrub_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_scrub_value(child) for child in value]
    if isinstance(value, str):
        value = re.sub(r"(?<![A-Za-z0-9])/(?:Users|home)/[^\s\"']+", "<local-path>", value)
        value = re.sub(r"[A-Za-z]:\\Users\\[^\s\"']+", "<local-path>", value)
    return value


def _yaml_bytes(value: Any) -> bytes:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=False).encode("utf-8")


def _write_archive(path: Path, entries: dict[str, bytes]) -> None:
    epoch_raw = os.environ.get("SOURCE_DATE_EPOCH", "0")
    try:
        mtime = int(epoch_raw)
    except ValueError as exc:
        raise TamesuError("SOURCE_DATE_EPOCH must be an integer") from exc
    directories: set[str] = set()
    for name in entries:
        parent = PurePosixPath(name).parent
        while str(parent) != ".":
            directories.add(parent.as_posix() + "/")
            parent = parent.parent
    names = sorted([*directories, *entries], key=lambda value: value.encode("utf-8"))
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=mtime, compresslevel=9) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                    for name in names:
                        info = tarfile.TarInfo(name)
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        info.mtime = mtime
                        if name.endswith("/"):
                            info.type = tarfile.DIRTYPE
                            info.mode = 0o755
                            info.size = 0
                            archive.addfile(info)
                        else:
                            data = entries[name]
                            info.type = tarfile.REGTYPE
                            info.mode = 0o644
                            info.size = len(data)
                            archive.addfile(info, io.BytesIO(data))
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_archive(path: Path) -> tuple[list[tarfile.TarInfo], dict[str, bytes]]:
    members: list[tarfile.TarInfo] = []
    files: dict[str, bytes] = {}
    expanded = 0
    seen: set[str] = set()
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                name = member.name
                pure = PurePosixPath(name)
                if not name or pure.is_absolute() or ".." in pure.parts or "\\" in name:
                    raise TamesuError(f"Unsafe archive path: {name!r}")
                normalized = pure.as_posix().rstrip("/")
                if name.rstrip("/") != normalized:
                    raise TamesuError(f"Non-canonical archive path: {name!r}")
                if normalized in seen:
                    raise TamesuError(f"Duplicate archive entry: {normalized}")
                seen.add(normalized)
                if not (member.isfile() or member.isdir()):
                    raise TamesuError(f"Unsupported archive entry type: {name}")
                members.append(member)
                if member.isdir():
                    continue
                if len(files) + 1 > MAX_FILES:
                    raise TamesuError(f"Archive contains more than {MAX_FILES} files")
                if member.size > MAX_FILE_SIZE:
                    raise TamesuError(f"Archive file exceeds {MAX_FILE_SIZE} bytes: {name}")
                expanded += member.size
                if expanded > MAX_EXPANDED_SIZE:
                    raise TamesuError(f"Archive expands beyond {MAX_EXPANDED_SIZE} bytes")
                stream = archive.extractfile(member)
                if stream is None:
                    raise TamesuError(f"Could not read archive entry: {name}")
                data = stream.read(MAX_FILE_SIZE + 1)
                if len(data) != member.size:
                    raise TamesuError(f"Archive entry size mismatch: {name}")
                files[normalized] = data
    except (tarfile.TarError, OSError) as exc:
        raise TamesuError(f"Could not read package archive: {exc}") from exc
    file_names = set(files)
    for name in file_names:
        parent = PurePosixPath(name).parent
        while str(parent) != ".":
            if parent.as_posix() in file_names:
                raise TamesuError(
                    f"Archive file conflicts with child entry: {parent.as_posix()}"
                )
            parent = parent.parent
    return members, files


def _validate_package_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise TamesuError("package.yml must contain a mapping")
    expected_fields = {
        "schema_version",
        "content_digest",
        "profile",
        "tamesu_version",
        "inventory",
        "publication",
    }
    if set(manifest) != expected_fields:
        raise TamesuError("package.yml has unrecognized or missing fields")
    if manifest.get("schema_version") != 1:
        raise TamesuError("Unsupported package schema_version")
    if manifest.get("profile") not in PROFILES:
        raise TamesuError("package.yml contains an invalid profile")
    if not isinstance(manifest.get("tamesu_version"), str) or not manifest["tamesu_version"]:
        raise TamesuError("package.yml contains an invalid tamesu_version")
    if not _valid_digest(manifest.get("content_digest")):
        raise TamesuError("package.yml contains an invalid content_digest")
    inventory = manifest.get("inventory")
    if not isinstance(inventory, list):
        raise TamesuError("package.yml inventory must be a list")
    previous: bytes | None = None
    seen: set[str] = set()
    for row in inventory:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
            raise TamesuError("package.yml contains an invalid inventory entry")
        path = row["path"]
        if not isinstance(path, str) or not path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise TamesuError("package.yml contains an unsafe inventory path")
        encoded = path.encode("utf-8")
        if path in seen or (previous is not None and encoded <= previous):
            raise TamesuError("package.yml inventory must contain unique bytewise-sorted paths")
        seen.add(path)
        previous = encoded
        if not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
            raise TamesuError("package.yml contains an invalid inventory hash")
        if isinstance(row["size"], bool) or not isinstance(row["size"], int) or row["size"] < 0:
            raise TamesuError("package.yml contains an invalid inventory size")
    publication = manifest.get("publication")
    if not isinstance(publication, dict) or publication.get("profile") != manifest.get("profile"):
        raise TamesuError("package.yml publication metadata does not match its profile")
    publication_fields = {
        "schema_version", "name", "publisher", "version", "title", "summary",
        "authors", "license", "tags", "profile", "data_review", "forked_from",
    }
    if set(publication) - publication_fields:
        raise TamesuError("package.yml publication metadata has unrecognized fields")
    required = {"schema_version", "name", "publisher", "version", "title", "summary", "authors", "license", "profile"}
    if not required.issubset(publication) or publication.get("schema_version") != 1:
        raise TamesuError("package.yml contains invalid publication metadata")
    if any(not isinstance(publication.get(key), str) or not publication[key].strip() for key in ("title", "summary", "license")):
        raise TamesuError("package.yml publication title, summary, and license are required")
    if any(not isinstance(publication.get(key), str) or not ID_PATTERN.fullmatch(publication[key]) for key in ("name", "publisher")):
        raise TamesuError("package.yml publication names must use lowercase kebab-case")
    if not isinstance(publication.get("version"), str) or not SEMVER.fullmatch(publication["version"]):
        raise TamesuError("package.yml publication version is invalid")
    if publication.get("profile") in {"full", "rescorable"}:
        review = publication.get("data_review")
        if not isinstance(review, dict) or set(review) != {"acknowledged", "note"} or review.get("acknowledged") is not True or not isinstance(review.get("note"), str) or not review["note"].strip():
            raise TamesuError("package.yml publication data review is missing")
    authors = publication.get("authors")
    if not isinstance(authors, list) or not authors or any(
        not isinstance(author, dict)
        or set(author) != {"name"}
        or not isinstance(author.get("name"), str)
        or not author["name"].strip()
        for author in authors
    ):
        raise TamesuError("package.yml publication authors are invalid")
    tags = publication.get("tags", [])
    if (
        not isinstance(tags, list)
        or len(tags) != len(set(tags))
        or any(not isinstance(tag, str) or not ID_PATTERN.fullmatch(tag) for tag in tags)
    ):
        raise TamesuError("package.yml publication tags are invalid")
    parent = publication.get("forked_from")
    if parent is not None:
        if not isinstance(parent, dict) or set(parent) != {
            "publisher", "name", "version", "content_digest"
        }:
            raise TamesuError("package.yml publication forked_from is invalid")
        if any(not isinstance(parent.get(key), str) or not ID_PATTERN.fullmatch(parent[key]) for key in ("publisher", "name")):
            raise TamesuError("package.yml publication fork identity is invalid")
        if not isinstance(parent.get("version"), str) or not SEMVER.fullmatch(parent["version"]):
            raise TamesuError("package.yml publication fork version is invalid")
        if not _valid_digest(parent.get("content_digest")):
            raise TamesuError("package.yml publication fork digest is invalid")


def _validate_payload_manifests(
    payload: dict[str, bytes], profile: str, publication: dict[str, Any]
) -> None:
    if "publication.yml" in payload:
        raise TamesuError(
            "payload/publication.yml is forbidden; publication metadata belongs in package.yml"
        )
    case = _payload_yaml(payload, "case.yml", required=True)
    if case.get("schema_version") != 1:
        raise TamesuError("payload/case.yml has an unsupported schema_version")
    if not isinstance(case.get("name"), str) or not ID_PATTERN.fullmatch(case["name"]):
        raise TamesuError("payload/case.yml has an invalid case name")
    if publication.get("name") != case.get("name"):
        raise TamesuError("package publication.name does not match payload case.name")
    for path in sorted(payload):
        filename = PurePosixPath(path).name
        if filename not in {"dataset.yml", "eval.yml", "run.yml", "report.yml", "result.yml", "package-view.yml"}:
            continue
        manifest = _payload_yaml(payload, path, required=True)
        if manifest.get("schema_version") != 1:
            raise TamesuError(f"payload/{path} has an unsupported schema_version")
    if profile == "report-only":
        view = _payload_yaml(payload, "package-view.yml", required=True)
        if view.get("view_only") is not True:
            raise TamesuError("report-only package-view.yml must set view_only: true")
        return
    for path in sorted(payload):
        if PurePosixPath(path).name != "eval.yml":
            continue
        evaluation = _payload_yaml(payload, path, required=True)
        base = PurePosixPath(path).parent
        references: list[tuple[str, Any]] = [
            ("dataset", evaluation.get("dataset")),
            ("output_schema", evaluation.get("output_schema")),
        ]
        for index, arm in enumerate(evaluation.get("arms", [])):
            if not isinstance(arm, dict):
                continue
            for role, raw in (arm.get("prompts") or {}).items():
                references.append((f"arms[{index}].prompts.{role}", raw))
            for index2, raw in enumerate(arm.get("references", [])):
                references.append((f"arms[{index}].references[{index2}]", raw))
        for label, raw in references:
            if not isinstance(raw, str) or not raw:
                raise TamesuError(f"payload/{path} contains an invalid {label} reference")
            resolved = _normalize_payload_reference(base, raw)
            if resolved not in payload:
                raise TamesuError(f"payload/{path} has a dangling {label} reference: {raw}")
    for path in sorted(payload):
        if PurePosixPath(path).name != "dataset.yml":
            continue
        dataset = _payload_yaml(payload, path, required=True)
        asset_paths: list[str] = []
        _collect_string_leaves(dataset.get("shared_assets"), asset_paths)
        for item in dataset.get("items", []):
            if isinstance(item, dict):
                _collect_string_leaves(item.get("assets"), asset_paths)
        for raw in asset_paths:
            resolved = _normalize_payload_reference(PurePosixPath(path).parent, raw)
            if resolved not in payload:
                raise TamesuError(f"payload/{path} has a dangling asset reference: {raw}")


def _payload_yaml(payload: dict[str, bytes], path: str, *, required: bool) -> dict[str, Any]:
    data = payload.get(path)
    if data is None:
        if required:
            raise TamesuError(f"Payload is missing {path}")
        return {}
    try:
        value = yaml.safe_load(data)
    except yaml.YAMLError as exc:
        raise TamesuError(f"Invalid YAML in payload/{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TamesuError(f"payload/{path} must contain a mapping")
    return value


def _normalize_payload_reference(base: PurePosixPath, raw: str) -> str:
    parts: list[str] = []
    for part in (base / PurePosixPath(raw)).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise TamesuError(f"Payload reference escapes the case: {raw}")
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts).as_posix()


def _collect_string_leaves(value: Any, output: list[str]) -> None:
    if isinstance(value, str):
        output.append(value)
    elif isinstance(value, list):
        for child in value:
            _collect_string_leaves(child, output)
    elif isinstance(value, dict):
        for child in value.values():
            _collect_string_leaves(child, output)


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
