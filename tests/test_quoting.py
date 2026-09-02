import email
import email.policy

from emailmcp.imap.quoting import build_html_quote, build_text_quote

HEADERS = "From: Alice <alice@example.com>\nDate: Mon, 10 Aug 2026 09:00:00 +0200\n"


def item(content_type: str, body: str, headers: str = HEADERS):
    raw = f'{headers}Subject: Hi\nContent-Type: {content_type}; charset="utf-8"\n\n{body}'
    return email.message_from_bytes(raw.encode("utf-8"), policy=email.policy.default)


def test_a_text_quote_deepens_the_history_it_already_carries():
    quote = build_text_quote(item("text/plain", "my answer\n\n> older message\n"))
    lines = quote.splitlines()
    assert lines[0] == "> On Mon, 10 Aug 2026 09:00:00 +0200, Alice <alice@example.com> wrote:"
    assert "> my answer" in lines
    assert ">" in lines  # a blank line becomes a bare marker
    assert ">> older message" in lines  # one more level, not a flattened one


def test_an_html_quote_wraps_the_original_and_escapes_the_attribution():
    assert "<blockquote><p>hello</p></blockquote>" in build_html_quote(
        item("text/html", "<p>hello</p>")
    )
    # No HTML part: the plain body is escaped into one.
    assert "first line<br>" in build_html_quote(item("text/plain", "first line\nsecond"))

    hostile = HEADERS.replace("Alice <alice@example.com>", '"<script>" <a@b.cz>')
    quote = build_html_quote(item("text/plain", "x", headers=hostile))
    assert "<script>" not in quote and "&lt;script&gt;" in quote

    assert build_text_quote(item("text/plain", "")) == ""
    assert build_html_quote(item("text/plain", "")) == ""
