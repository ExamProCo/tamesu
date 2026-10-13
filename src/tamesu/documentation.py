"""Read and atomically edit the authored fields of project documents."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import yaml

from .config import _validate_case, load_yaml, validate_experiment
from .errors import ConfigError

FIELDS = {'case.yml': ('name', 'title', 'description', 'business_use', 'current_problem', 'technical_uncertainty'),
          'experiment.yml': ('name', 'description', 'hypothesis', 'success_criteria'),
          'eval.yml': ('question', 'description')}


def document_path(root: Path, ref: str) -> Path:
    paths = {path.relative_to(root).as_posix(): path for pattern in ('cases/*/case.yml', 'cases/*/experiments/*/experiment.yml', 'cases/*/experiments/*/evals/*/eval.yml') for path in root.glob(pattern)}
    if ref not in paths or not paths[ref].resolve().is_relative_to(root.resolve()):
        raise ConfigError('Unknown authored document')
    return paths[ref]


def read_document(root: Path, ref: str) -> dict:
    path = document_path(root, ref)
    data = load_yaml(path)
    return {'path': ref, 'revision': hashlib.sha256(path.read_bytes()).hexdigest(),
            'fields': {key: data.get(key, '') for key in FIELDS[path.name]}}


def save_document(root: Path, ref: str, revision: str, fields: dict) -> dict:
    path = document_path(root, ref)
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != revision:
        raise ConfigError('This file changed on disk. Reload it before saving.')
    if not isinstance(fields, dict) or set(fields) - set(FIELDS[path.name]):
        raise ConfigError('Only authored documentation fields can be edited')
    data = load_yaml(path)
    for key, value in fields.items():
        if key == 'success_criteria':
            if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v.strip() for v in value):
                raise ConfigError('success_criteria must be a list of non-empty strings')
        elif not isinstance(value, str):
            raise ConfigError(f'{key} must be text')
        if value or key in data:
            data[key] = value
    errors = []
    if path.name == 'case.yml':
        _validate_case(data, path.parent, errors)
    elif path.name == 'experiment.yml':
        validate_experiment(data, errors)
    else:
        for key in ('question', 'description'):
            if not isinstance(data.get(key), str) or not data[key].strip():
                errors.append(f'{key} must contain text')
    if errors:
        raise ConfigError('; '.join(errors))
    # Replace only changed top-level field spans; preserve comments and unrelated config.
    text = original.decode('utf-8')
    lines = text.splitlines(keepends=True)
    nodes = yaml.compose(text)
    spans = {key.value: (key.start_mark.line, value.end_mark.line + (1 if value.end_mark.column else 0)) for key, value in nodes.value}
    replacements = []
    appended = []
    for key in fields:
        if data.get(key) == yaml.safe_load(text).get(key):
            continue
        rendered = yaml.safe_dump({key: data[key]}, sort_keys=False, allow_unicode=True)
        if key in spans:
            replacements.append((*spans[key], rendered))
        elif key in data:
            appended.append(rendered)
    for start, end, rendered in sorted(replacements, reverse=True):
        lines[start:end] = [rendered]
    content = ''.join(lines)
    if appended:
        content = content.rstrip('\n') + '\n' + ''.join(appended)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.chmod(path.stat().st_mode)
        if path.read_bytes() != original:
            raise ConfigError('This file changed on disk. Reload it before saving.')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return read_document(root, ref)
