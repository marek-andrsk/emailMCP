import base64
import email
import email.policy

import messages

from emailmcp.imap import images
from emailmcp.imap.content import ImageSegment, TextSegment, render

REF = "andrsk.cz:1"
PNG = base64.b64encode(messages.png(300, 200)).decode()

SVG_MAIL = """From: a@b.cz
Subject: Org chart
MIME-Version: 1.0
Content-Type: multipart/related; boundary="r"

--r
Content-Type: text/html; charset="utf-8"

<p>Here is the chart:</p><img src="cid:chart@x"><p>Thoughts?</p>
--r
Content-Type: image/svg+xml
Content-ID: <chart@x>
Content-Disposition: inline

<svg xmlns="http://www.w3.org/2000/svg"><text>Revenue 42</text></svg>
--r--
"""


RELATED = (
    'From: a@b.cz\nSubject: x\nMIME-Version: 1.0\n'
    'Content-Type: multipart/related; boundary="r"\n\n'
    '--r\nContent-Type: text/html; charset="utf-8"\n\n{html}\n'
    "--r\nContent-Type: image/png\nContent-ID: <c@d>\n"
    "Content-Transfer-Encoding: base64\nContent-Disposition: inline\n\n" + PNG + "\n--r--\n"
)


def build(raw: str):
    return email.message_from_bytes(raw.encode("utf-8"), policy=email.policy.default)


def texts(body):
    return [segment.text for segment in body.segments if isinstance(segment, TextSegment)]


def test_a_message_without_inline_images_reads_exactly_as_before():
    plain = build('From: a@b.cz\nContent-Type: text/plain; charset="utf-8"\n\nAhoj,\n\njak se máš?\n')
    assert render(plain, REF).segments == [TextSegment("Ahoj,\n\njak se máš?")]

    quoted = build(
        'From: a@b.cz\nContent-Type: text/plain; charset="utf-8"\n\n'
        "my answer\n\nOn Mon, 10 Aug 2026, Alice wrote:\n> older\n"
    )
    assert texts(render(quoted, REF)) == ["my answer"]

    # HTML wins only when it carries something plain text cannot express.
    assert texts(render(messages.with_remote_image(), REF)) == ["plain fallback"]


def test_inline_images_land_between_the_text_they_belong_to():
    """The whole point: an image read where it was written, not in a detached
    list at the end."""
    body = render(messages.with_inline_images(messages.png(300, 200), messages.png(320, 240)), REF)

    assert [type(s).__name__ for s in body.segments] == [
        "TextSegment", "ImageSegment", "TextSegment", "ImageSegment", "TextSegment",
    ]
    assert [s.part_id for s in body.segments if isinstance(s, ImageSegment)] == ["2.2", "2.3"]
    assert texts(body) == ["before 0", "after 0\n\nbefore 1", "after 1"]
    assert body.attachments == []  # an inlined image is not repeated as a file


def test_the_markup_around_an_image_never_leaks_into_the_body():
    """html2text escapes brackets inside alt text and wraps a clickable image
    in a link. Consuming only the inner ![](...) leaves its syntax behind."""
    linked = build(RELATED.format(html='<a href="http://x.com">see <img src="cid:c@d"> now</a>'))
    assert [type(s).__name__ for s in render(linked, REF).segments] == [
        "TextSegment", "ImageSegment", "TextSegment"
    ]
    # The href is a click tracker and is dropped; the words around it are not.
    assert texts(render(linked, REF)) == ["see", "now"]

    escaped = build(RELATED.format(html='<p>a</p><img src="cid:c@d" alt="x]y">'))
    body = render(escaped, REF)
    assert texts(body) == ["a"]
    assert [s.part_id for s in body.segments if isinstance(s, ImageSegment)] == ["2"]


def test_images_worth_nothing_leave_no_trace():
    # Layout furniture, and remote images that must not be fetched — the
    # request would itself be the tracking event the sender is waiting for.
    body = render(messages.with_inline_images(messages.png(20, 20)), REF)
    assert texts(body) == ["before 0\n\nafter 0"]
    assert body.attachments == []

    remote = render(messages.with_remote_image(), REF)
    assert "tracker.example" not in " ".join(texts(remote))


def test_an_image_that_is_not_shown_is_still_recoverable():
    over_budget = render(
        messages.with_inline_images(messages.png(300, 200), messages.png(320, 240)),
        REF,
        images.Budget(max_images=1),
    )
    announced = [text for text in texts(over_budget) if "image not shown" in text]
    assert len(announced) == 1
    assert 'read_attachment("andrsk.cz:1", "2.3")' in announced[0]

    # Nothing here can decode SVG, so it keeps its place in the text AND stays
    # an attachment — read_attachment can still serve its markup.
    svg = render(build(SVG_MAIL), REF)
    assert 'read_attachment("andrsk.cz:1", "2")' in texts(svg)[0]
    assert [part.content_type for part in svg.attachments] == ["image/svg+xml"]


def test_a_file_the_body_never_refers_to_is_an_attachment():
    invoice = render(messages.with_attachment(b"%PDF-1.4", "faktura.pdf", "application", "pdf"), REF)
    assert [part.filename for part in invoice.attachments] == ["faktura.pdf"]
    assert texts(invoice) == ["V priloze posilam fakturu."]

    photo = render(messages.with_attachment(messages.png(300, 200), "photo.png", "image", "png"), REF)
    assert [part.filename for part in photo.attachments] == ["photo.png"]
    assert not any(isinstance(segment, ImageSegment) for segment in photo.segments)
