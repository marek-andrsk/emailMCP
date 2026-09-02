import email
import email.policy
from datetime import datetime, timezone

from emailmcp.imap import mime


def build(raw: str):
    # Parse from bytes, as the store does — message_from_string re-encodes
    # payloads via raw-unicode-escape and would corrupt non-ASCII bodies.
    return email.message_from_bytes(raw.encode("utf-8"), policy=email.policy.default)


def part(content_type: str, payload: str) -> str:
    return f'--b\nContent-Type: {content_type}; charset="utf-8"\n\n{payload}\n'


def multipart(subtype: str, *parts: str) -> str:
    body = "".join(parts)
    return (
        "From: Alice <alice@example.com>\nSubject: Hi\nMIME-Version: 1.0\n"
        f'Content-Type: multipart/{subtype}; boundary="b"\n\n{body}--b--\n'
    )


PLAIN = "From: Alice <a@b.cz>\nContent-Type: text/plain; charset=\"utf-8\"\n\nAhoj,\njak se máš?\n"


def test_the_body_is_the_first_part_that_actually_carries_content():
    """One predicate decides what counts as content, applied when the part is
    picked. Deciding it later let a whitespace-only alternative shadow the
    real body and reply drafts went out with an empty quote."""
    assert mime.parse_body(build(PLAIN)) == "Ahoj,\njak se máš?"

    # Plain wins over HTML, but only when it is not blank.
    both = multipart("alternative", part("text/plain", "plain version"), part("text/html", "<p>html</p>"))
    assert mime.parse_body(build(both)) == "plain version"

    blank = multipart("alternative", part("text/plain", "\t"), part("text/html", "<p>the real content</p>"))
    assert "the real content" in mime.parse_body(build(blank))

    two_plain = multipart("mixed", part("text/plain", ""), part("text/plain", "the actual body"))
    assert mime.parse_body(build(two_plain)) == "the actual body"

    assert "<p>" not in mime.parse_body(build(multipart("mixed", part("text/html", "<p>Only html</p>"))))
    assert "<p>html</p>" in mime.extract_html(build(both))
    assert mime.extract_html(build(PLAIN)) is None
    assert mime.extract_html(build(multipart("alternative", part("text/html", "   ")))) is None


def test_quoted_history_is_cut_at_the_attribution():
    assert mime.strip_quoted_reply("My answer\n\nOn Mon, Alice wrote:\n> original") == "My answer"
    assert mime.strip_quoted_reply("Answer\n-----Original Message-----\nFrom: Alice") == "Answer"
    assert mime.strip_quoted_reply("Line one\n\nLine two") == "Line one\n\nLine two"
    assert mime.text_to_html("a <b>\nc") == "a &lt;b&gt;<br>c"


def test_concurrent_html_conversion_stays_isolated():
    """A shared HTML2Text instance interleaves output between threads, and
    FastMCP runs every sync tool in a worker-thread pool."""
    import sys
    import threading

    documents = {tag: "<div><p>" + (tag * 4 + " ") * 300 + "</p></div>" for tag in "ABCD"}
    failures = []
    original_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # force preemption mid-conversion

    def convert(tag):
        try:
            for _ in range(80):
                out = mime.html_to_text(documents[tag])
                if out.count(tag * 4) != 300:
                    failures.append(f"{tag}: wrong count {out.count(tag * 4)}")
                for other in "ABCD":
                    if other != tag and other * 4 in out:
                        failures.append(f"{tag}: leaked {other}")
        except Exception as exc:  # parser assertions from shared state
            failures.append(f"{tag}: {exc!r}")

    try:
        threads = [threading.Thread(target=convert, args=(tag,)) for tag in "ABCD"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(original_interval)

    assert not failures, failures[:5]


class Addr:
    def __init__(self, name, mailbox, host):
        self.name, self.mailbox, self.host = name, mailbox, host


class Envelope:
    def __init__(self, from_=None, subject=None):
        self.from_, self.subject, self.message_id, self.date = from_, subject, None, None


def test_header_and_envelope_values_are_decoded_once_and_centrally():
    assert mime.decode_header("=?utf-8?q?P=C5=99=C3=ADjem?=") == "Příjem"
    assert mime.decode_header(None) == ""
    assert mime.is_from("Marek <ME@Andrsk.CZ>", "me@andrsk.cz")
    assert not mime.is_from("Alice <alice@example.com>", "me@andrsk.cz")
    assert not mime.is_from("", "me@andrsk.cz")

    assert mime.envelope_from(Envelope([Addr(b"Alice", b"alice", b"example.com")])) == (
        "Alice <alice@example.com>"
    )
    assert mime.envelope_from(Envelope([Addr(None, b"alice", b"example.com")])) == "alice@example.com"
    assert mime.envelope_subject(Envelope()) == "(no subject)"

    when = datetime(2026, 8, 10, 9, 5, tzinfo=timezone.utc)
    assert mime.message_date({b"INTERNALDATE": when}) == when
    assert mime.message_date({}) is None
    assert mime.date_key(None) < mime.date_key(datetime(2026, 1, 1, 12, 0)) < mime.date_key(when)
    assert mime.format_date(None) == ""

    snippet = mime.snippet({b"BODY[TEXT]<0>": b"  hello \n\n  world  " + b"x" * 300})
    assert snippet.startswith("hello world") and len(snippet) <= mime.SNIPPET_LENGTH
    assert mime.snippet({}) == ""
    assert mime.flag_set({b"FLAGS": [b"\\Seen"]}) == {"\\Seen"}
