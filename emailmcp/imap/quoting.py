"""Quoted thread blocks appended to drafts.

Both builders take an already-parsed message so the store can fetch it once and
feed the same object to the text and HTML variants.
"""

from __future__ import annotations

import email.message
import html as html_lib

from . import mime


def _attribution(message: email.message.Message) -> str:
    return (
        f"On {mime.decode_header(message.get('Date', ''))}, "
        f"{mime.decode_header(message.get('From', ''))} wrote:"
    )


def build_text_quote(message: email.message.Message) -> str:
    body = mime.parse_body(message)
    if not body:
        return ""

    lines = [f"> {_attribution(message)}"]
    for line in body.splitlines():
        if not line.strip():
            lines.append(">")
        elif line.lstrip().startswith(">"):
            # Preserve the existing quote depth by adding one more level.
            lines.append(f">{line}")
        else:
            lines.append(f"> {line}")
    return "\n".join(lines)


def build_html_quote(message: email.message.Message) -> str:
    html_body = mime.extract_html(message)
    if not html_body:
        text_body = mime.parse_body(message)
        if not text_body:
            return ""
        html_body = mime.text_to_html(text_body)

    header = f'<div class="quote-header">{html_lib.escape(_attribution(message))}</div>'
    return f"{header}<blockquote>{html_body}</blockquote>"
