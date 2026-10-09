"""One-shot C rendering engine, compiled and run in a Docker sandbox.

Deterministic score, five equal steps: exported, compiled, ran, rendered (a decodable image),
and plausible (the image is not blank, flat, or near-uniform).

Tamesu contract (see plans/INSPECT-AI.md):
  * the dataset is the frozen JSONL Tamesu writes and passes as the `dataset` argument;
  * limits, retries, model and effort come from eval.yml, not from this file;
  * artifacts are staged through tamesu.inspect_support while the sandbox is alive.
"""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.scorer import Score, Target, accuracy, scorer
from inspect_ai.solver import TaskState, generate
from inspect_ai.util import sandbox

from tamesu.inspect_support import stager

HERE = Path(__file__).parent
FILE_BLOCK = re.compile(r"```[a-zA-Z]*\n\s*//\s*FILE:\s*([\w./-]+)\n(.*?)```", re.S)


@task
def rendering_engine(dataset: str) -> Task:
    return Task(
        dataset=json_dataset(dataset),
        solver=[generate()],
        scorer=compiles_and_renders(),
        sandbox=("docker", str(HERE / "compose.yaml")),
        message_limit=4,
    )


@scorer(metrics=[accuracy()])
def compiles_and_renders():
    """Ordered partial credit: exported, compiled, ran, rendered, plausible (0.2 each)."""

    async def score(state: TaskState, target: Target) -> Score:
        box = sandbox()
        stage = stager(state.sample_id)
        stage.add("response", "response.txt", state.output.completion, "text/plain")
        passed: dict[str, bool] = {}
        details: dict[str, object] = {}
        files = FILE_BLOCK.findall(state.output.completion)
        details["files"] = [name for name, _ in files]
        safe = [
            (name, body)
            for name, body in files
            if not name.startswith("/") and ".." not in name
        ]
        if not safe:
            return Score(value=0.0, explanation="no usable FILE blocks", metadata=details)
        for name, body in safe:
            await box.write_file(name, body)
            stage.add("source", f"source/{name}", body, "text/x-c")
        passed["exported"] = True

        build = await box.exec(["sh", "-c", "gcc -O2 -o render *.c -lm 2>&1"], timeout=120)
        stage.add("compiler-log", "compiler.txt", build.stdout + build.stderr, "text/plain")
        passed["compiled"] = build.success
        details["compile_returncode"] = build.returncode

        if build.success:
            try:
                run = await box.exec(["./render"], timeout=120)
            except (PermissionError, TimeoutError) as exc:
                stage.add("run-log", "run.txt", f"{type(exc).__name__}: {exc}", "text/plain")
                details["run_error"] = type(exc).__name__
            else:
                tail = (run.stdout[-4000:] + run.stderr[-4000:]) or "(no output)"
                stage.add("run-log", "run.txt", tail, "text/plain")
                passed["ran"] = run.success
                details["run_returncode"] = run.returncode

        if passed.get("ran"):
            # read_file() uses `docker cp`, which cannot see tmpfs mounts: export via exec.
            dump = await box.exec(["sh", "-c", "base64 out.ppm"], timeout=60)
            if dump.success:
                png = _ppm_to_png(base64.b64decode(dump.stdout))
                if png is not None:
                    stage.add("render", "render.png", png, "image/png")
                    passed["rendered"] = True
                    stats = image_stats(png)
                    details["image"] = stats
                    passed["plausible"] = stats["plausible"]
            else:
                details["render_error"] = dump.stderr.strip()[:200]

        order = ["exported", "compiled", "ran", "rendered", "plausible"]
        return Score(
            value=sum(passed.get(step, False) for step in order) / len(order),
            answer=", ".join(step for step in order if passed.get(step)),
            explanation=str(details),
            metadata=details,
        )

    return score


def _ppm_to_png(data: bytes) -> bytes | None:
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return None
    out = io.BytesIO()
    image.convert("RGB").save(out, format="PNG")
    return out.getvalue()


# Thresholds are deliberately loose: they catch blank, flat, and noise-free test patterns,
# not poor art. Quality is the human review's job.
MIN_SIDE = 64
MIN_UNIQUE_COLORS = 32
MAX_DOMINANT_FRACTION = 0.90
MIN_LUMINANCE_STDDEV = 8.0
LUMINANCE_RANGE = (10.0, 245.0)


def image_stats(png: bytes) -> dict[str, object]:
    from PIL import Image, ImageStat

    with Image.open(io.BytesIO(png)) as image:
        rgb = image.convert("RGB")
    width, height = rgb.size
    colors = rgb.getcolors(maxcolors=width * height) or []
    unique = len(colors)
    dominant = max((count for count, _ in colors), default=0) / max(width * height, 1)
    luminance = ImageStat.Stat(rgb.convert("L"))
    mean, stddev = luminance.mean[0], luminance.stddev[0]
    plausible = (
        min(width, height) >= MIN_SIDE
        and unique >= MIN_UNIQUE_COLORS
        and dominant <= MAX_DOMINANT_FRACTION
        and stddev >= MIN_LUMINANCE_STDDEV
        and LUMINANCE_RANGE[0] <= mean <= LUMINANCE_RANGE[1]
    )
    return {
        "width": width,
        "height": height,
        "unique_colors": unique,
        "dominant_color_fraction": round(dominant, 4),
        "luminance_mean": round(mean, 2),
        "luminance_stddev": round(stddev, 2),
        "plausible": plausible,
    }
