"""Normalising mail images into something worth spending context on.

Mail carries images at whatever size the sender's camera produced and at
whatever tininess a tracking pixel needs. Every image handed to a model goes
through here: decoded once — which also proves the bytes really are an image
and not a mislabelled part — scaled down, and re-encoded to a single known
format. One path, so a photo, a screenshot and a signature logo all arrive in
the same shape.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

# Beyond this a model gains nothing from the extra pixels.
MAX_DIMENSION = 1568
# Below this an image is layout furniture: tracking pixels, spacers, bullets.
MIN_DIMENSION = 50
MIN_SOURCE_BYTES = 5 * 1024
# Transparency stops being worth its bytes somewhere around here.
MAX_ENCODED_BYTES = 1_500_000
JPEG_QUALITY = 85

MAX_IMAGES = 8
MAX_TOTAL_BYTES = 6 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RenderedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    decorative: bool


class Budget:
    """The image payload one response may spend.

    A single newsletter can carry more pixels than the whole context is worth.
    What does not fit is announced in the text rather than silently dropped, so
    the model knows there is something it can still ask for by part id.
    """

    def __init__(self, max_images: int = MAX_IMAGES, max_bytes: int = MAX_TOTAL_BYTES) -> None:
        self._images = max_images
        self._bytes = max_bytes

    def take(self, image: RenderedImage) -> bool:
        if self._images <= 0 or len(image.data) > self._bytes:
            return False
        self._images -= 1
        self._bytes -= len(image.data)
        return True


def render(data: bytes) -> RenderedImage | None:
    """Decode, downscale and re-encode. ``None`` when the bytes are not an image."""
    frame = _decode(data)
    if frame is None:
        return None
    decorative = _is_decorative(len(data), frame)
    frame = _downscale(frame)
    encoded, mime_type = _encode(frame)
    return RenderedImage(encoded, mime_type, frame.width, frame.height, decorative)


def _is_decorative(source_size: int, frame: Image.Image) -> bool:
    """True for images that carry layout, not content.

    Judged on the source. A wide banner downscales to a few dozen pixels tall,
    and measuring it afterwards would file it away as a spacer.
    """
    return min(frame.size) < MIN_DIMENSION or source_size < MIN_SOURCE_BYTES


def _decode(data: bytes) -> Image.Image | None:
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            # Animated formats decode to their first frame, which is the one
            # frame that ever means anything in a mail body.
            return source.convert("RGBA" if _has_alpha(source) else "RGB")
    except Exception:
        return None


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info


def _downscale(frame: Image.Image) -> Image.Image:
    longest = max(frame.size)
    if longest <= MAX_DIMENSION:
        return frame
    scale = MAX_DIMENSION / longest
    size = (max(1, round(frame.width * scale)), max(1, round(frame.height * scale)))
    return frame.resize(size, Image.LANCZOS)


def _encode(frame: Image.Image) -> tuple[bytes, str]:
    if frame.mode == "RGBA":
        png = _save(frame, "PNG", optimize=True)
        if len(png) <= MAX_ENCODED_BYTES:
            return png, "image/png"
        frame = _flatten(frame)
    return _save(frame, "JPEG", quality=JPEG_QUALITY, optimize=True), "image/jpeg"


def _flatten(frame: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", frame.size, (255, 255, 255))
    canvas.paste(frame, mask=frame.getchannel("A"))
    return canvas


def _save(frame: Image.Image, image_format: str, **options) -> bytes:
    buffer = io.BytesIO()
    frame.save(buffer, format=image_format, **options)
    return buffer.getvalue()
