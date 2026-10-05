from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from typing import Any, Callable

from PIL import Image, ImageStat, UnidentifiedImageError

ALLOWED_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}
FORMAT_NAMES = {"png": "PNG", "jpeg": "JPEG", "jpg": "JPEG", "webp": "WEBP"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000


class IntegrityFailure(Exception):
    """A returned image failed a mandatory integrity check; `reason` is a stable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class DecodedImage:
    media_type: str
    extension: str
    width: int
    height: int
    mode: str


def inspect_image(data: bytes) -> DecodedImage:
    """Decode bytes and apply the mandatory integrity checks.

    The bytes are authoritative: the type comes from decoding, never from a provider claim.
    """
    if not data:
        raise IntegrityFailure("empty_bytes", "the provider returned zero bytes")
    if len(data) > MAX_IMAGE_BYTES:
        raise IntegrityFailure(
            "size_limit_exceeded", f"image is {len(data)} bytes; the limit is {MAX_IMAGE_BYTES}"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                detected = image.format or ""
                width, height = image.size
                mode = image.mode
                if detected not in ALLOWED_FORMATS:
                    raise IntegrityFailure(
                        "disallowed_format", f"{detected or 'unknown'} is not an allowed raster format"
                    )
                if width < 1 or height < 1:
                    raise IntegrityFailure(
                        "degenerate_dimensions", f"image dimensions are {width}x{height}"
                    )
                if width * height > MAX_IMAGE_PIXELS:
                    raise IntegrityFailure(
                        "size_limit_exceeded",
                        f"image has {width * height} pixels; the limit is {MAX_IMAGE_PIXELS}",
                    )
                image.load()
    except IntegrityFailure:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise IntegrityFailure("decompression_bomb", str(exc)) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:
        raise IntegrityFailure("undecodable", f"image could not be decoded: {exc}") from exc
    media_type, extension = ALLOWED_FORMATS[detected]
    return DecodedImage(media_type, extension, width, height, mode)


@dataclass(frozen=True)
class ScorerContext:
    requested_format: str | None


Scorer = Callable[[bytes, DecodedImage, dict[str, Any], ScorerContext], dict[str, Any]]


def _decodable_image(data, decoded, params, context):
    return {"pass": True, "detail": {"media_type": decoded.media_type}}


def _min_resolution(data, decoded, params, context):
    need_w, need_h = int(params["width"]), int(params["height"])
    return {
        "pass": decoded.width >= need_w and decoded.height >= need_h,
        "detail": {
            "width": decoded.width,
            "height": decoded.height,
            "required_width": need_w,
            "required_height": need_h,
        },
    }


def _format_match(data, decoded, params, context):
    wanted = FORMAT_NAMES.get((context.requested_format or "").lower())
    if wanted is None:
        return {"pass": True, "detail": {"requested": None, "note": "no output_format requested"}}
    actual = ALLOWED_FORMATS[wanted][0]
    return {
        "pass": actual == decoded.media_type,
        "detail": {"requested": actual, "actual": decoded.media_type},
    }


def _open_pixels(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _non_uniform(data, decoded, params, context):
    threshold = float(params.get("min_stddev", 2.0))
    with _open_pixels(data) as image:
        stddevs = ImageStat.Stat(image.convert("RGBA")).stddev
    spread = max(stddevs)
    return {
        "pass": spread > threshold,
        "detail": {"max_channel_stddev": round(spread, 4), "min_stddev": threshold},
    }


def _has_transparency(data, decoded, params, context):
    require = bool(params.get("require", True))
    with _open_pixels(data) as image:
        rgba = image.convert("RGBA")
        low, _high = rgba.getchannel("A").getextrema()
    transparent = low < 255
    return {
        "pass": transparent == require,
        "detail": {"has_transparency": transparent, "required": require},
    }


SCORERS: dict[str, Scorer] = {
    "decodable_image": _decodable_image,
    "min_resolution": _min_resolution,
    "format_match": _format_match,
    "non_uniform": _non_uniform,
    "has_transparency": _has_transparency,
}


def validate_scorer_params(name: str, params: dict[str, Any], location: str, errors: list[str]) -> None:
    allowed = {"prerequisite"}
    if "prerequisite" in params and not isinstance(params["prerequisite"], bool):
        errors.append(f"{location}.prerequisite must be a boolean")
    if name == "min_resolution":
        allowed |= {"width", "height"}
        for key in ("width", "height"):
            value = params.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append(f"{location}.{key} must be a positive integer")
    elif name == "non_uniform":
        allowed |= {"min_stddev"}
        value = params.get("min_stddev", 2.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            errors.append(f"{location}.min_stddev must be a non-negative number")
    elif name == "has_transparency":
        allowed |= {"require"}
        if "require" in params and not isinstance(params["require"], bool):
            errors.append(f"{location}.require must be a boolean")
    for key in sorted(set(params) - allowed):
        errors.append(f"{location}.{key} is not a recognized parameter")


def run_scorers(
    data: bytes,
    decoded: DecodedImage,
    declared: list[tuple[str, dict[str, Any]]],
    context: ScorerContext,
) -> dict[str, Any]:
    scores: dict[str, Any] = {"decodable_image": SCORERS["decodable_image"](data, decoded, {}, context)}
    for name, params in declared:
        scores[name] = SCORERS[name](data, decoded, params, context)
    return scores
