"""Shell completion scripts and project-aware candidate discovery."""
from __future__ import annotations

from pathlib import Path

from .config import load_eval_context
from .discovery import discover_eval_paths, eval_id_from_path, resolve_eval_ref
from .errors import TamesuError

COMMANDS = ('case', 'exp', 'eval', 'lint', 'plan', 'run', 'status', 'show', 'report', 'analyze', 'completion')


def candidates(root: Path, words: list[str]) -> list[str]:
    prefix = words[-1] if words else ''
    previous = words[-2] if len(words) > 1 else ''
    command = words[0] if words else ''
    evaluations = [eval_id_from_path(root, path) for path in discover_eval_paths(root)]
    values = list(COMMANDS)
    if previous == '--arm':
        values = []
        if len(words) > 1:
            try:
                context = load_eval_context(root, resolve_eval_ref(root, words[1]))
                values = [arm['id'] for arm in context.evaluation['arms']]
            except TamesuError:
                pass
    elif previous in ('--model', '--only'):
        from .providers.models import MODELS
        values = list(MODELS)
    elif command == 'show' or (command == 'exp' and previous == 'new'):
        values = [path.parent.name + ('/' if command == 'exp' else '') for path in root.glob('cases/*/case.yml')]
    elif command == 'eval' and 'new' in words:
        values = [f'{path.parents[2].name}/{path.parent.name}/' for path in root.glob('cases/*/experiments/*/experiment.yml')]
    elif command in ('case', 'exp', 'eval') and len(words) <= 2:
        values = ['new']
    elif command in ('plan', 'run', 'status', 'report', 'analyze', 'resume'):
        values = evaluations
    return sorted(value for value in set(values) if value.startswith(prefix))


def shell_script(shell: str) -> str:
    if shell == 'bash':
        return '''_tamesu_complete() {
    local IFS=$'\\n'
    COMPREPLY=($(tamesu _complete "${COMP_WORDS[@]:1:COMP_CWORD}"))
}
complete -F _tamesu_complete tamesu
'''
    return '''#compdef tamesu
_tamesu_complete() {
    local -a matches
    matches=("${(@f)$(tamesu _complete "${words[@]:1}")}")
    compadd -Q -- "${matches[@]}"
}
compdef _tamesu_complete tamesu
'''
