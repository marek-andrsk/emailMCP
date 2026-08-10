import email
import email.policy

from emailmcp.imap.quoting import QuotedMessage, build_html_quote, build_text_quote

PLAIN = """From: Alice <alice@example.com>
Date: Mon, 10 Aug 2026 09:00:00 +0200
Subject: Hi
Content-Type: text/plain; charset="utf-8"

first line

second line
"""

ALREADY_QUOTED = """From: Alice <alice@example.com>
Date: Mon, 10 Aug 2026 09:00:00 +0200
Subject: Re: Hi
Content-Type: text/plain; charset="utf-8"

my answer
> older message
"""

HTML = """From: Alice <alice@example.com>
Date: Mon, 10 Aug 2026 09:00:00 +0200
Subject: Hi
Content-Type: text/html; charset="utf-8"

<p>hello</p>
"""

EMPTY = """From: Alice <alice@example.com>
Date: Mon, 10 Aug 2026 09:00:00 +0200
Subject: Hi
Content-Type: text/plain; charset="utf-8"

"""


def item(raw: str, uid: int = 1):
    return QuotedMessage(
        uid, email.message_from_bytes(raw.encode("utf-8"), policy=email.policy.default)
    )


def test_text_quote_has_attribution_and_quoted_lines():
    quote = build_text_quote([item(PLAIN)], 1)
    lines = quote.splitlines()
    assert lines[0].startswith("> On Mon, 10 Aug 2026")
    assert "Alice <alice@example.com> wrote:" in lines[0]
    assert "> first line" in lines
    assert ">" in lines  # the blank line becomes a bare marker


def test_text_quote_deepens_existing_quote_levels():
    quote = build_text_quote([item(ALREADY_QUOTED)], 1)
    assert ">> older message" in quote


def test_text_quote_trims_history_of_other_thread_messages():
    # uid 2 is not the message being replied to, so its own quotes are stripped
    quote = build_text_quote([item(ALREADY_QUOTED, uid=2)], 1)
    assert "older message" not in quote
    assert "> my answer" in quote


def test_empty_bodies_are_skipped():
    assert build_text_quote([item(EMPTY)], 1) == ""
    assert build_html_quote([item(EMPTY)], 1) == ""


def test_html_quote_wraps_the_original_html():
    quote = build_html_quote([item(HTML)], 1)
    assert "<blockquote><p>hello</p></blockquote>" in quote
    assert 'class="quote-header"' in quote


def test_html_quote_falls_back_to_escaped_text():
    quote = build_html_quote([item(PLAIN)], 1)
    assert "first line<br>" in quote


def test_html_quote_escapes_the_attribution():
    raw = PLAIN.replace("Alice <alice@example.com>", '"<script>" <a@b.cz>')
    quote = build_html_quote([item(raw)], 1)
    assert "<script>" not in quote
    assert "&lt;script&gt;" in quote


def test_multiple_messages_are_joined():
    quote = build_text_quote([item(PLAIN, uid=1), item(PLAIN, uid=2)], 1)
    assert quote.count("wrote:") == 2
