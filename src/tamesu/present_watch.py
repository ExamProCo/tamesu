"""`present --watch`: re-render when a file in the case changes. Polling, no dependencies."""
from __future__ import annotations

import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .errors import TamesuError

SKIP_DIRS = {"build", ".git", "__pycache__", "node_modules", ".tamesu", "staging"}


def snapshot(case_dir: Path) -> dict[str, tuple[int, int]]:
    """(mtime_ns, size) of every file that can change the page. Inspect transcripts are skipped."""
    state: dict[str, tuple[int, int]] = {}
    for root, dirs, files in os.walk(case_dir):
        relative = Path(root).relative_to(case_dir)
        in_run = len(relative.parts) >= 2 and relative.parts[-2] == "runs"
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (in_run and d == "inspect")]
        for name in files:
            path = Path(root) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            state[str(path.relative_to(case_dir))] = (stat.st_mtime_ns, stat.st_size)
    return state


def watch(
    case_dir: Path,
    render: Callable[[], Path],
    *,
    interval: float = 1.0,
    debounce: float = 0.3,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[str], None] = print,
    max_renders: int | None = None,
) -> int:
    """Render once, then again after each change. Returns the number of renders."""
    renders = 0

    def once() -> None:
        nonlocal renders
        started = time.monotonic()
        try:
            destination = render()
            emit(f"Rendered {destination} in {time.monotonic() - started:.2f}s")
        except Exception as exc:  # noqa: BLE001 - a bad edit must not end the session
            emit(f"error: {exc}")
        renders += 1

    previous = snapshot(case_dir)
    once()
    while max_renders is None or renders < max_renders:
        sleep(interval)
        current = snapshot(case_dir)
        if current == previous:
            continue
        sleep(debounce)  # let a multi-file save settle
        previous = snapshot(case_dir)
        once()
    return renders


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass


def serve(directory: Path, port: int) -> ThreadingHTTPServer:
    """Serve the rendered page on localhost from a background thread. Port 0 picks a free one."""
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), partial(_QuietHandler, directory=str(directory)))
    except OSError as exc:
        raise TamesuError(f"Cannot serve on port {port} ({exc.strerror}); pass --port with another number.") from exc
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
