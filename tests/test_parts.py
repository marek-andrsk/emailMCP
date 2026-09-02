import email
import email.policy

import messages
from emailmcp.imap import parts

PLAIN = b'From: a@b.cz\nSubject: x\nContent-Type: text/plain; charset="utf-8"\n\nbody\n'


def build(raw: bytes):
    return email.message_from_bytes(raw, policy=email.policy.default)


def test_part_ids_follow_the_message_structure():
    """A part id has to survive the round trip out to the model and back, so it
    is the IMAP body section rather than an index into a flattened walk."""
    assert [p.section for p in parts.inventory(build(PLAIN))] == ["1"]

    nested = messages.with_inline_images(messages.png(80, 80))
    assert [p.section for p in parts.inventory(nested)] == ["1", "2.1", "2.2"]
    assert parts.find(nested, "2.2").content_id == "img0@mail"  # angle brackets stripped
    assert parts.find(nested, "7.2") is None

    # message/rfc822 is multipart-shaped but is one attached file.
    forwarded = messages.with_attachment(PLAIN, "fwd.eml", "message", "rfc822")
    assert [p.section for p in parts.inventory(forwarded)] == ["1", "2"]
    assert b"body" in parts.find(forwarded, "2").data


def test_only_content_disposition_decides_what_is_a_body_part():
    """Several mailers put name= on the body part too. Reading that as "this is
    a file" moves the body into the attachment list and empties read_email."""
    named = build(
        b'From: a@b.cz\nSubject: x\nMIME-Version: 1.0\n'
        b'Content-Type: multipart/mixed; boundary="m"\n\n'
        b'--m\nContent-Type: text/plain; charset="utf-8"; name="message.txt"\n\nthe body\n'
        b'--m\nContent-Type: text/plain; charset="utf-8"\n'
        b'Content-Disposition: attachment; filename="notes.txt"\n\nattached\n--m--\n'
    )
    body, attached = parts.inventory(named)
    assert (body.is_body, body.text.strip()) == (True, "the body")
    assert attached.is_body is False


def test_a_part_knows_whether_it_can_be_shown_or_read():
    csv = parts.find(messages.with_attachment(b"a;b;c", "data.csv", "text", "csv"), "2")
    assert csv.describe() == {
        "part_id": "2",
        "filename": "data.csv",
        "content_type": "text/csv",
        "size": 5,
    }
    assert (csv.is_text, csv.is_image, csv.is_body) == (True, False, False)

    png = parts.find(messages.with_inline_images(messages.png(80, 80)), "2.2")
    assert (png.is_image, png.is_text) == (True, False)

    # SVG is markup: readable as source, not decodable as pixels.
    svg = parts.find(messages.with_attachment(b"<svg/>", "c.svg", "image", "svg+xml"), "2")
    assert (svg.is_image, svg.is_text) == (True, True)
