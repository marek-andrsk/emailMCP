"""Quoted thread blocks appended to drafts.

Both builders take already-parsed messages so the store can fetch each message
once and feed the same objects to the text and HTML variants.
"""

from __future__ import annotations

import email.message
import html as html_lib
from dataclasses import dataclass
from typing import Sequence

from . import mime


@dataclass(frozen=True, slots=True)
class QuotedMessage:
    uid: int
    message: email.message.Message


def _body_for(item: QuotedMessage, reply_to_uid: int) -> str:
    body = mime.parse_body(item.message)
    # The message actually being replied to keeps its own quoted history; older
    # messages in the thread are trimmed so the draft does not nest endlessly.
    if item.uid == reply_to_uid:
        return body
    return mime.strip_quoted_reply(body)


def _attribution(message: email.message.Message) -> tuple[str, str]:
    return (
        mime.decode_header(message.get("Date", "")),
        mime.decode_header(message.get("From", "")),
    )


def build_text_quote(messages: Sequence[QuotedMessage], reply_to_uid: int) -> str:
    blocks: list[str] = []

    for item in messages:
        body = _body_for(item, reply_to_uid)
        if not body:
            continue

        date_hdr, from_hdr = _attribution(item.message)
        lines = [f"> On {date_hdr}, {from_hdr} wrote:"]
        for line in body.splitlines():
            if not line.strip():
                lines.append(">")
            elif line.lstrip().startswith(">"):
                # Preserve the existing quote depth by adding one more level.
                lines.append(f">{line}")
            else:
                lines.append(f"> {line}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def build_html_quote(messages: Sequence[QuotedMessage], reply_to_uid: int) -> str:
    blocks: list[str] = []

    for item in messages:
        html_body = mime.extract_html(item.message)
        if not html_body:
            text_body = _body_for(item, reply_to_uid)
            if not text_body:
                continue
            html_body = mime.text_to_html(text_body)

        date_hdr, from_hdr = _attribution(item.message)
        header = (
            '<div class="quote-header">'
            f"On {html_lib.escape(date_hdr)}, {html_lib.escape(from_hdr)} wrote:"
            "</div>"
        )
        blocks.append(f"{header}<blockquote>{html_body}</blockquote>")

    return "<br><br>".join(blocks)
