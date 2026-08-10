import email
import email.policy
from datetime import datetime, timezone

from emailmcp.imap import mime


def build(raw: str):
    # Parse from bytes, as the store does — message_from_string re-encodes
    # payloads via raw-unicode-escape and would corrupt non-ASCII bodies.
    return email.message_from_bytes(raw.encode("utf-8"), policy=email.policy.default)


PLAIN = """From: Alice <alice@example.com>
To: me@andrsk.cz
Subject: Hello
Content-Type: text/plain; charset="utf-8"

Ahoj,
jak se máš?
"""

ALTERNATIVE = """From: Alice <alice@example.com>
Subject: Hi
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="b"

--b
Content-Type: text/plain; charset="utf-8"

plain version
--b
Content-Type: text/html; charset="utf-8"

<p>html <b>version</b></p>
--b--
"""

HTML_ONLY = """From: Alice <alice@example.com>
Subject: Hi
Content-Type: text/html; charset="utf-8"

<p>Only <b>html</b> here</p>
"""


def test_parse_body_plain_preserves_utf8():
    assert mime.parse_body(build(PLAIN)) == "Ahoj,\njak se máš?"


EMPTY_FIRST_PART = """From: Alice <alice@example.com>
Subject: Fwd
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="m"

--m
Content-Type: text/plain; charset="utf-8"

--m
Content-Type: text/plain; charset="utf-8"

skutečný obsah zprávy
--m--
"""


BLANK_PLAIN_ALTERNATIVE = """From: Alice <alice@example.com>
Subject: Newsletter
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="x"

--x
Content-Type: text/plain; charset="utf-8"


\t
--x
Content-Type: text/html; charset="utf-8"

<p>The real content of the message</p>
--x--
"""

BLANK_FIRST_OF_TWO_PLAIN = """From: Alice <alice@example.com>
Subject: Fwd
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="m"

--m
Content-Type: text/plain; charset="utf-8"

\t
--m
Content-Type: text/plain; charset="utf-8"

the actual body
--m--
"""


def test_an_empty_leading_plain_part_does_not_shadow_the_real_body():
    assert mime.parse_body(build(EMPTY_FIRST_PART)) == "skutečný obsah zprávy"


def test_a_whitespace_only_plain_part_does_not_discard_the_html():
    body = mime.parse_body(build(BLANK_PLAIN_ALTERNATIVE))
    assert "The real content of the message" in body


def test_a_whitespace_only_plain_part_does_not_shadow_a_later_one():
    assert mime.parse_body(build(BLANK_FIRST_OF_TWO_PLAIN)) == "the actual body"


def test_extract_html_skips_a_blank_html_part():
    raw = BLANK_PLAIN_ALTERNATIVE.replace(
        '<p>The real content of the message</p>', '   \n'
    )
    assert mime.extract_html(build(raw)) is None


def test_parse_body_prefers_plain_over_html():
    assert mime.parse_body(build(ALTERNATIVE)) == "plain version"


def test_parse_body_falls_back_to_html():
    body = mime.parse_body(build(HTML_ONLY))
    assert "Only" in body and "html" in body
    assert "<p>" not in body


def test_concurrent_html_conversion_stays_isolated():
    """A shared HTML2Text instance interleaves output between threads."""
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


def test_extract_html_finds_the_html_part():
    assert "<b>version</b>" in mime.extract_html(build(ALTERNATIVE))


def test_extract_html_returns_none_for_plain_only():
    assert mime.extract_html(build(PLAIN)) is None


def test_strip_quoted_reply_drops_quotes_and_attribution():
    body = "My answer\n\nOn Mon, Alice wrote:\n> original\n> text"
    assert mime.strip_quoted_reply(body) == "My answer"


def test_strip_quoted_reply_drops_outlook_separator():
    body = "Answer\n-----Original Message-----\nFrom: Alice"
    assert mime.strip_quoted_reply(body) == "Answer"


def test_strip_quoted_reply_keeps_an_unquoted_body():
    body = "Line one\n\nLine two"
    assert mime.strip_quoted_reply(body) == body


def test_text_to_html_escapes_and_breaks_lines():
    assert mime.text_to_html("a <b>\nc") == "a &lt;b&gt;<br>c"


def test_decode_header_handles_encoded_words():
    assert mime.decode_header("=?utf-8?q?P=C5=99=C3=ADjem?=") == "Příjem"


def test_decode_header_of_none_is_empty():
    assert mime.decode_header(None) == ""


def test_is_from_matches_case_insensitively():
    assert mime.is_from("Marek <ME@Andrsk.CZ>", "me@andrsk.cz")
    assert not mime.is_from("Alice <alice@example.com>", "me@andrsk.cz")
    assert not mime.is_from("", "me@andrsk.cz")


def test_date_key_orders_naive_and_aware_dates():
    naive = datetime(2026, 1, 1, 12, 0)
    aware = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    assert mime.date_key(None) < mime.date_key(naive) < mime.date_key(aware)


def test_format_date_of_none_is_empty():
    assert mime.format_date(None) == ""
    assert mime.format_date(datetime(2026, 8, 10, 9, 5)) == "2026-08-10 09:05"


def test_snippet_collapses_whitespace_and_truncates():
    data = {b"BODY[TEXT]<0>": b"  hello \n\n  world  " + b"x" * 300}
    result = mime.snippet(data, length=20)
    assert result.startswith("hello world")
    assert "\n" not in result


def test_snippet_of_missing_body_is_empty():
    assert mime.snippet({}) == ""


def test_flag_set_decodes_bytes():
    assert mime.flag_set({b"FLAGS": [b"\\Seen", b"\\Answered"]}) == {"\\Seen", "\\Answered"}


class _Addr:
    def __init__(self, name, mailbox, host):
        self.name = name
        self.mailbox = mailbox
        self.host = host


class _Envelope:
    def __init__(self, from_=None, subject=None, message_id=None):
        self.from_ = from_
        self.subject = subject
        self.message_id = message_id
        self.date = None


def test_envelope_from_formats_name_and_address():
    envelope = _Envelope(from_=[_Addr(b"Alice", b"alice", b"example.com")])
    assert mime.envelope_from(envelope) == "Alice <alice@example.com>"


def test_envelope_from_without_a_name():
    envelope = _Envelope(from_=[_Addr(None, b"alice", b"example.com")])
    assert mime.envelope_from(envelope) == "alice@example.com"


def test_envelope_subject_falls_back():
    assert mime.envelope_subject(_Envelope()) == "(no subject)"
    assert mime.envelope_subject(_Envelope(subject=b"Hi")) == "Hi"


def test_message_date_prefers_internaldate():
    when = datetime(2026, 8, 10, tzinfo=timezone.utc)
    assert mime.message_date({b"INTERNALDATE": when}) == when
    assert mime.message_date({}) is None
