"""The body of an email as an ordered run of text and images.

An inline image only means what it means where it sits: the screenshot under
the sentence that introduces it, the chart the paragraph points at. So the body
is rendered once, as markdown carrying one marker per image, and split on those
markers afterwards — which also means quote stripping and every other
whole-body transform still sees a single continuous text.
"""

from __future__ import annotations

import email.message
import re
from dataclasses import dataclass

from . import images, mime, parts
from .images import Budget, RenderedImage
from .parts import MessagePart

_IMAGE_MARKER = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]*)[^)]*\)")
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class TextSegment:
    text: str


@dataclass(frozen=True, slots=True)
class ImageSegment:
    part_id: str
    filename: str
    image: RenderedImage


Segment = TextSegment | ImageSegment


@dataclass(frozen=True, slots=True)
class EmailBody:
    segments: list[Segment]
    attachments: list[MessagePart]


def render(msg: email.message.Message, ref: str, budget: Budget | None = None) -> EmailBody:
    """Render a message body, inlining the images it refers to."""
    budget = budget or Budget()
    inventory = parts.inventory(msg)
    by_cid = {part.content_id.lower(): part for part in inventory if part.is_image and part.content_id}

    markdown = _markdown(inventory, by_cid)
    segments, inlined = _split(mime.strip_quoted_reply(markdown), by_cid, budget, ref)

    attachments = [
        part for part in inventory if not part.is_body and part.section not in inlined
    ]
    return EmailBody(segments, attachments)


def _markdown(inventory: list[MessagePart], by_cid: dict[str, MessagePart]) -> str:
    """The body as markdown, from whichever alternative carries the most.

    HTML wins only when it actually references inline images, because that is
    the one thing the plain-text alternative cannot express.
    """
    html = _first_body(inventory, "text/html")
    plain = _first_body(inventory, "text/plain")

    if html is not None and _references_images(html.text, by_cid):
        return mime.html_to_markdown(html.text)
    if plain is not None:
        return plain.text.strip()
    if html is not None:
        return mime.html_to_text(html.text)
    return ""


def _first_body(inventory: list[MessagePart], content_type: str) -> MessagePart | None:
    for part in inventory:
        if part.is_body and part.content_type == content_type and part.text.strip():
            return part
    return None


def _references_images(html: str, by_cid: dict[str, MessagePart]) -> bool:
    lowered = html.lower()
    return any(f"cid:{cid}" in lowered for cid in by_cid)


def _split(
    markdown: str,
    by_cid: dict[str, MessagePart],
    budget: Budget,
    ref: str,
) -> tuple[list[Segment], set[str]]:
    segments: list[Segment] = []
    inlined: set[str] = set()
    cursor = 0

    for match in _IMAGE_MARKER.finditer(markdown):
        before = markdown[cursor : match.start()]
        cursor = match.end()

        part = _resolve(match.group(1), by_cid)
        if part is None:
            # A remote image cannot be fetched — and must not be, since the
            # request is itself the tracking event the sender is waiting for.
            _add_text(segments, before)
            continue

        image = images.render(part.data)
        if image is None:
            # Nothing to inline, but read_attachment may still be able to serve
            # it, so it keeps its place in the text and stays an attachment.
            _add_text(segments, before + _placeholder(part, ref))
            continue

        # Everything below is accounted for in the body, so none of it is
        # repeated in the attachment list.
        inlined.add(part.section)
        if image.decorative:
            _add_text(segments, before)
        elif budget.take(image):
            _add_text(segments, before)
            segments.append(ImageSegment(part.section, part.filename, image))
        else:
            _add_text(segments, before + _placeholder(part, ref))

    _add_text(segments, markdown[cursor:])
    return [segment for segment in map(_trimmed, segments) if segment is not None], inlined


def _resolve(source: str, by_cid: dict[str, MessagePart]) -> MessagePart | None:
    lowered = source.lower()
    if not lowered.startswith("cid:"):
        return None
    return by_cid.get(lowered[4:])


def _add_text(segments: list[Segment], text: str) -> None:
    """Append text, merging with a preceding text run so images stay the seams."""
    if not text:
        return
    if segments and isinstance(segments[-1], TextSegment):
        segments[-1] = TextSegment(segments[-1].text + text)
    else:
        segments.append(TextSegment(text))


def _trimmed(segment: Segment) -> Segment | None:
    if not isinstance(segment, TextSegment):
        return segment
    # Markup conversion and dropped image markers both leave holes behind.
    text = _BLANK_RUN.sub("\n\n", segment.text).strip()
    return TextSegment(text) if text else None


def _placeholder(part: MessagePart, ref: str) -> str:
    name = part.filename or part.content_type
    return (
        f'\n\n[image not shown: {name}, {_human_size(part.size)} — '
        f'read_attachment("{ref}", "{part.section}")]\n\n'
    )


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} kB"
    return f"{size / (1024 * 1024):.1f} MB"
