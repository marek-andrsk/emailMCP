"""Pure MIME and header parsing.

Nothing here touches IMAP or any connection state, so every function can be
exercised directly from a raw message in a test.
"""

from __future__ import annotations

import email
import email.header
import email.message
import email.utils
import html as html_lib
from datetime import datetime, timezone

import html2text

SNIPPET_LENGTH = 150


def _make_converter() -> html2text.HTML2Text:
    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0
    return converter


def html_to_text(value: str) -> str:
    # A fresh converter per call. HTML2Text carries parser state across
    # handle(), and FastMCP runs sync tools in a worker-thread pool, so a
    # shared instance interleaves output between concurrently converted
    # messages (and trips its own parser assertions). Constructing one costs
    # roughly 6% of a conversion, which is not worth a lock.
    return _make_converter().handle(value).strip()


def _decode_payload(part: email.message.Message) -> str | None:
    payload = part.get_payload(decode=True)
    # An empty payload counts as absent: otherwise an empty leading text/plain
    # part would latch and shadow the real body later in the message.
    if not payload:
        return None
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _first_usable_part(msg: email.message.Message, content_type: str) -> str | None:
    """First part of this type whose payload actually carries content.

    One predicate decides what counts as content, applied when the part is
    picked. Deciding that later, on a part already chosen, is what let a
    whitespace-only alternative shadow the real body.
    """
    for part in msg.walk():
        if part.get_content_type() != content_type:
            continue
        text = _decode_payload(part)
        if text and text.strip():
            return text
    return None


def parse_body(msg: email.message.Message) -> str:
    """Extract a readable body. Prefers plain text, falls back to HTML."""
    if not msg.is_multipart():
        content_type = msg.get_content_type()
        text = _decode_payload(msg)
        if text is None:
            return ""
        if content_type == "text/plain":
            return text.strip()
        if content_type == "text/html":
            return html_to_text(text)
        return ""

    plain = _first_usable_part(msg, "text/plain")
    if plain:
        return plain.strip()
    html_part = _first_usable_part(msg, "text/html")
    if html_part:
        return html_to_text(html_part)
    return ""


def extract_html(msg: email.message.Message) -> str | None:
    """Return the first HTML part of a message, if it has one."""
    if not msg.is_multipart():
        if msg.get_content_type() != "text/html":
            return None
        text = _decode_payload(msg)
        return text.strip() if text else None

    found = _first_usable_part(msg, "text/html")
    return found.strip() if found else None


def strip_quoted_reply(body: str) -> str:
    """Remove quoted reply chains to keep context clean."""
    result: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if stripped.startswith("On ") and stripped.endswith("wrote:"):
            break
        if stripped.startswith("----") and "Original Message" in line:
            break
        if stripped.startswith("From:") and len(result) > 3:
            break
        result.append(line)
    return "\n".join(result).strip()


def text_to_html(text: str) -> str:
    return html_lib.escape(text).replace("\n", "<br>")


def decode_header(value: str | None) -> str:
    if not value:
        return ""
    decoded = []
    for part, charset in email.header.decode_header(value):
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def parse_addr(value: str | None) -> str:
    if not value:
        return ""
    _, addr = email.utils.parseaddr(value)
    return addr.lower()


def is_from(header_value: str | None, address: str) -> bool:
    """True when a From header belongs to ``address``."""
    sender = parse_addr(header_value)
    own = parse_addr(address) or (address or "").strip().lower()
    return bool(sender) and sender == own


# --- IMAP ENVELOPE helpers -------------------------------------------------
# ENVELOPE fields arrive as bytes; these keep the decoding in one place.


def _decode_bytes(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def format_envelope_address(addr) -> str:
    name = _decode_bytes(getattr(addr, "name", None))
    mailbox = _decode_bytes(getattr(addr, "mailbox", None))
    host = _decode_bytes(getattr(addr, "host", None))
    email_addr = f"{mailbox}@{host}" if mailbox or host else ""
    return f"{name} <{email_addr}>" if name else email_addr


def envelope_from(envelope) -> str:
    senders = getattr(envelope, "from_", None)
    if not senders:
        return ""
    return format_envelope_address(senders[0])


def envelope_subject(envelope) -> str:
    raw = getattr(envelope, "subject", None)
    if not raw:
        return "(no subject)"
    return decode_header(_decode_bytes(raw))


def envelope_message_id(envelope) -> str:
    return _decode_bytes(getattr(envelope, "message_id", None))


def message_date(data: dict) -> datetime | None:
    """Best-effort timestamp for a fetched message (INTERNALDATE, else ENVELOPE)."""
    internal = data.get(b"INTERNALDATE")
    if isinstance(internal, datetime):
        return internal
    envelope = data.get(b"ENVELOPE")
    if envelope is not None and getattr(envelope, "date", None):
        return envelope.date
    return None


def date_key(value: datetime | None) -> float:
    """Sortable key that tolerates naive datetimes and missing dates."""
    if not value:
        return -1.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def format_date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def snippet(data: dict, length: int = SNIPPET_LENGTH) -> str:
    """Collapse the peeked body prefix into a one-line snippet."""
    raw = data.get(b"BODY[TEXT]<0>")
    if not raw:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return " ".join(str(raw)[:length].strip().split())


def flag_set(data: dict) -> set[str]:
    flags = data.get(b"FLAGS") or []
    return {f.decode() if isinstance(f, bytes) else f for f in flags}
