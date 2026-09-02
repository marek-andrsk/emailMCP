"""MIME part inventory.

The store already fetches whole messages, so every part's bytes are in hand and
this module only has to name and classify them. Part ids follow the IMAP
body-section convention (``2``, ``1.3``), so an id the model reads out of one
response still points at the same part when it asks for it on the next fetch.
"""

from __future__ import annotations

import email.message
from dataclasses import dataclass
from typing import Iterator

from . import mime

BODY_TYPES = ("text/plain", "text/html")
SVG = "image/svg+xml"


@dataclass(frozen=True, slots=True)
class MessagePart:
    section: str
    content_type: str
    filename: str
    content_id: str
    disposition: str
    charset: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def text(self) -> str:
        return mime.decode_text(self.data, self.charset)

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")

    @property
    def is_text(self) -> bool:
        """Readable as source. SVG is markup, and says more unrendered."""
        return self.content_type.startswith("text/") or self.content_type == SVG

    @property
    def is_body(self) -> bool:
        """A text part that is the message itself rather than a file sent along.

        Only Content-Disposition decides. The ``name=`` parameter on
        Content-Type is a legacy alias that plenty of mailers set on the body
        part too, and reading it as "this is a file" empties the body.
        """
        return self.content_type in BODY_TYPES and self.disposition != "attachment"

    def describe(self) -> dict:
        return {
            "part_id": self.section,
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
        }


def inventory(msg: email.message.Message) -> list[MessagePart]:
    return [_build(section, part) for section, part in _leaves(msg)]


def find(msg: email.message.Message, section: str) -> MessagePart | None:
    for part in inventory(msg):
        if part.section == section:
            return part
    return None


def _leaves(msg: email.message.Message) -> Iterator[tuple[str, email.message.Message]]:
    if not msg.is_multipart():
        yield "1", msg
        return
    yield from _children(msg, "")


def _children(container: email.message.Message, prefix: str) -> Iterator[tuple[str, email.message.Message]]:
    for index, part in enumerate(container.get_payload(), start=1):
        section = f"{prefix}{index}"
        # message/rfc822 is multipart-shaped but is one attached file, not a
        # container of parts the recipient is meant to address individually.
        if part.is_multipart() and part.get_content_type() != "message/rfc822":
            yield from _children(part, f"{section}.")
        else:
            yield section, part


def _build(section: str, part: email.message.Message) -> MessagePart:
    return MessagePart(
        section=section,
        content_type=part.get_content_type(),
        filename=part.get_filename() or "",
        content_id=_content_id(part),
        disposition=part.get_content_disposition() or "",
        charset=part.get_content_charset() or "utf-8",
        data=_payload(part),
    )


def _content_id(part: email.message.Message) -> str:
    raw = str(part.get("Content-ID") or "").strip()
    if raw.startswith("<") and raw.endswith(">"):
        return raw[1:-1]
    return raw


def _payload(part: email.message.Message) -> bytes:
    raw = part.get_payload(decode=True)
    if raw is not None:
        return raw
    # message/rfc822: the payload is the embedded message object, not bytes.
    nested = part.get_payload()
    if isinstance(nested, list) and nested:
        return nested[0].as_bytes()
    return b""
