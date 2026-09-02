"""Message builders carrying real image bytes, shared by the parsing tests."""

import io
import os
from email.message import EmailMessage

from PIL import Image

SENDER = "Alice <alice@example.com>"
RECIPIENT = "me@andrsk.cz"


def png(width: int, height: int) -> bytes:
    """Noise, so the encoded size is realistic rather than compressed to nothing."""
    image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _headers(msg: EmailMessage, subject: str) -> EmailMessage:
    msg["From"] = SENDER
    msg["To"] = RECIPIENT
    msg["Subject"] = subject
    msg["Date"] = "Mon, 10 Aug 2026 09:00:00 +0200"
    msg["Message-ID"] = "<i1@example.com>"
    return msg


def with_inline_images(*images: bytes, subject: str = "Screenshots") -> EmailMessage:
    """multipart/alternative → [text/plain, multipart/related → [html, images...]]."""
    msg = _headers(EmailMessage(), subject)
    msg.set_content("plain fallback")

    body = "".join(
        f"<p>before {index}</p><img src=\"cid:img{index}@mail\"><p>after {index}</p>"
        for index, _ in enumerate(images)
    )
    msg.add_alternative(f"<html><body>{body}</body></html>", subtype="html")

    related = msg.get_payload()[1]
    for index, image in enumerate(images):
        related.add_related(
            image, maintype="image", subtype="png", cid=f"<img{index}@mail>"
        )
    return msg


def with_attachment(data: bytes, filename: str, maintype: str, subtype: str) -> EmailMessage:
    msg = _headers(EmailMessage(), "Faktura")
    msg.set_content("V priloze posilam fakturu.")
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg


def with_remote_image() -> EmailMessage:
    msg = _headers(EmailMessage(), "Newsletter")
    msg.set_content("plain fallback")
    msg.add_alternative(
        '<html><body><p>hello</p><img src="https://tracker.example/px.gif">'
        "<p>bye</p></body></html>",
        subtype="html",
    )
    return msg
