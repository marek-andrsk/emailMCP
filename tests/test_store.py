import email
import email.policy
from datetime import datetime, timezone

import pytest

import messages
from fakes import ADDRESS, FakeIMAP, build_store, meta
from emailmcp.config import Mailbox
from emailmcp.imap.content import ImageSegment, TextSegment
from emailmcp.imap.store import MailStore, MessageNotFound, PartNotFound, UnsupportedPart

INCOMING = f"""From: Alice <alice@example.com>
To: {ADDRESS}
Date: Mon, 10 Aug 2026 09:00:00 +0200
Subject: Nabidka
Message-ID: <a1@example.com>
Content-Type: text/plain; charset="utf-8"

Ahoj, mam pro tebe nabidku.
""".encode()

INCOMING_HTML = f"""From: Bob <bob@example.com>
To: {ADDRESS}
Date: Mon, 10 Aug 2026 10:00:00 +0200
Subject: Re: Faktura
Message-ID: <b1@example.com>
References: <b0@example.com>
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="x"

--x
Content-Type: text/plain; charset="utf-8"

\t
--x
Content-Type: text/html; charset="utf-8"

<p>The real content of the message</p>
--x--
""".encode()


@pytest.fixture
def imap():
    when_a = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    when_b = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
    metadata = {
        1: meta("Alice", "alice@example.com", "Nabidka", "<a1@example.com>", when_a),
        2: meta("Bob", "bob@example.com", "Re: Faktura", "<b1@example.com>", when_b),
    }
    # Tuples, as imapclient's parse_response actually returns.
    return FakeIMAP(metadata, {1: INCOMING, 2: INCOMING_HTML}, threads=((1,), (2,)))


def store_with(imap, msg):
    imap.raw[3] = msg.as_bytes()
    return build_store(imap)


def parse_appended(imap, index=0):
    folder, raw, flags = imap.appended[index]
    return folder, email.message_from_bytes(raw, policy=email.policy.default), flags


# --- listing ---------------------------------------------------------------


def test_list_inbox_returns_one_row_per_thread_with_prefixed_ids(imap):
    rows = build_store(imap).list_inbox()
    assert [row["id"] for row in rows] == ["andrsk.cz:1", "andrsk.cz:2"]
    assert rows[0] == {
        "id": "andrsk.cz:1",
        "mailbox": "andrsk.cz",
        "from": "Alice <alice@example.com>",
        "subject": "Nabidka",
        "date": "2026-08-10 09:00",
        "snippet": "snippet text",
        "needs_reply": True,
        "thread_ids": ["andrsk.cz:1"],
    }

    imap.threads_result = [[1, 2]]
    threaded = build_store(imap).list_inbox()
    assert len(threaded) == 1
    assert threaded[0]["id"] == "andrsk.cz:2"  # the latest message represents it
    assert threaded[0]["thread_ids"] == ["andrsk.cz:1", "andrsk.cz:2"]

    imap.metadata = {}
    assert build_store(imap).list_inbox() == []


def test_needs_reply_clears_once_the_thread_has_been_handled(imap):
    imap.metadata[1][b"FLAGS"] = [b"\\Answered"]
    assert build_store(imap).list_inbox()[0]["needs_reply"] is False

    imap.metadata[1][b"FLAGS"] = []
    imap.sent_hits = {"<a1@example.com>"}  # a reply already sits in Sent
    rows = build_store(imap).list_inbox()
    assert [row["needs_reply"] for row in rows] == [False, True]

    imap.metadata[1] = meta("Marek", ADDRESS, "Nabidka", "<a1@example.com>", datetime.now(timezone.utc))
    assert build_store(imap).list_inbox()[0]["needs_reply"] is False

    store = build_store(imap)
    imap.searches.clear()
    store.list_inbox()
    store.list_inbox()
    # Message 1 is now our own, so only message 2 is probed — once, then cached.
    assert len([c for c in imap.searches if c != ["ALL"]]) == 1


# --- reading ---------------------------------------------------------------


def test_read_email_returns_metadata_and_body_segments(imap):
    imap.threads_result = [[1, 2]]
    result = build_store(imap).read_email(1)

    assert result.metadata["id"] == "andrsk.cz:1"
    assert result.metadata["from"] == "Alice <alice@example.com>"
    assert result.metadata["subject"] == "Nabidka"
    assert result.metadata["message_id"] == "<a1@example.com>"
    assert result.metadata["attachments"] == []
    assert result.segments == [TextSegment("Ahoj, mam pro tebe nabidku.")]
    # Thread context is oldest first, whatever order the server fetched it in.
    assert [e["id"] for e in result.metadata["thread_context"]] == ["andrsk.cz:1", "andrsk.cz:2"]

    with pytest.raises(MessageNotFound) as excinfo:
        build_store(imap).read_email(99)
    assert "andrsk.cz:99" in str(excinfo.value)


def test_read_attachment_serves_what_it_can_render_or_read(imap):
    inline = store_with(imap, messages.with_inline_images(messages.png(300, 200)))
    assert isinstance(inline.read_attachment(3, "2.2"), ImageSegment)

    csv = store_with(imap, messages.with_attachment(b"a;b;c", "data.csv", "text", "csv"))
    listed = csv.read_email(3).metadata["attachments"]
    assert listed == [{"part_id": "2", "filename": "data.csv", "content_type": "text/csv", "size": 5}]
    assert csv.read_attachment(3, listed[0]["part_id"]) == TextSegment("a;b;c")

    # SVG is markup, and says more to a reader unrendered than rasterised.
    svg = store_with(imap, messages.with_attachment(b"<svg><text>42</text></svg>", "c.svg", "image", "svg+xml"))
    assert svg.read_attachment(3, "2") == TextSegment("<svg><text>42</text></svg>")


def test_read_attachment_says_why_it_cannot_show_something(imap):
    pdf = store_with(imap, messages.with_attachment(b"%PDF-1.4", "f.pdf", "application", "pdf"))
    with pytest.raises(PartNotFound) as excinfo:
        pdf.read_attachment(3, "9")
    assert "andrsk.cz:3" in str(excinfo.value)
    assert "Readable parts: 2" in str(excinfo.value)

    with pytest.raises(UnsupportedPart) as excinfo:
        pdf.read_attachment(3, "2")
    assert "application/pdf" in str(excinfo.value) and "f.pdf" in str(excinfo.value)

    broken = store_with(imap, messages.with_attachment(b"not a png", "x.png", "image", "png"))
    with pytest.raises(UnsupportedPart) as excinfo:
        broken.read_attachment(3, "2")
    assert "could not be decoded" in str(excinfo.value)


# --- drafts ----------------------------------------------------------------


def test_a_reply_draft_threads_correctly_and_quotes_the_original(imap):
    store = build_store(imap)
    store.save_reply_draft(1, "Diky, ozvu se.")
    folder, draft, flags = parse_appended(imap)

    assert (folder, flags) == ("Drafts", [b"\\Draft"])
    assert draft["From"] == ADDRESS
    assert draft["To"] == "Alice <alice@example.com>"
    assert draft["Subject"] == "Re: Nabidka"
    assert draft["In-Reply-To"] == draft["References"] == "<a1@example.com>"
    assert draft.get_content_type() == "text/plain"

    body = draft.get_payload(decode=True).decode("utf-8")
    assert body.startswith("Diky, ozvu se.")
    assert "> Ahoj, mam pro tebe nabidku." in body


def test_a_reply_to_html_mail_quotes_the_html_in_both_halves(imap):
    """Message 2's text/plain part is whitespace only. Taking it at face value
    is how a draft goes out with an empty quote."""
    build_store(imap).save_reply_draft(2, "ok")
    _folder, draft, _flags = parse_appended(imap)

    assert draft.get_content_type() == "multipart/alternative"
    assert draft["References"] == "<b0@example.com> <b1@example.com>"
    assert draft["Subject"] == "Re: Faktura"  # no double Re:

    plain = next(p for p in draft.walk() if p.get_content_type() == "text/plain")
    assert "> The real content of the message" in plain.get_payload(decode=True).decode("utf-8")
    assert any(p.get_content_type() == "text/html" for p in draft.walk())


def test_an_identical_draft_is_saved_once_under_a_single_lock(imap):
    """The check, the record and the append are one critical section: a client
    retrying while the first save is still in flight is the case this exists
    for, and testing before recording would let both through."""
    store = build_store(imap)
    held = []
    original_append = imap.append

    def watching(folder, message, flags=None, msg_time=None):
        held.append(store._draft_lock.locked())
        original_append(folder, message, flags=flags, msg_time=msg_time)

    imap.append = watching
    store.save_reply_draft(1, "ok")
    assert held == [True]

    assert "duplicate suppressed" in store.save_reply_draft(1, "ok")
    store.save_reply_draft(1, "something else")
    assert len(imap.appended) == 2

    # A save that did not land leaves no record, so it can be retried at once.
    retrying = build_store(imap)
    calls = {"n": 0}

    def flaky(folder, message, flags=None, msg_time=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("append failed")
        original_append(folder, message, flags=flags, msg_time=msg_time)

    imap.append = flaky
    with pytest.raises(OSError):
        retrying.save_reply_draft(1, "retry me")
    retrying.save_reply_draft(1, "retry me")
    assert len(imap.appended) == 3


def test_a_new_draft_is_sent_from_its_own_mailbox(imap):
    message = build_store(imap).save_new_draft("someone@example.com", "Dotaz", "Text")
    _folder, draft, _flags = parse_appended(imap)
    assert (draft["From"], draft["To"], draft["Subject"]) == (ADDRESS, "someone@example.com", "Dotaz")
    assert "Marek" in message  # the label tells the user where it landed

    build_store(imap, drafts_folder="INBOX.Drafts").save_new_draft("a@b.cz", "s", "b")
    assert imap.appended[1][0] == "INBOX.Drafts"
    build_store(imap, drafts_folder=None).save_new_draft("a@b.cz", "s", "b")
    assert imap.appended[2][0] == "Drafts"  # discovered from the folder list


# --- against the real Connection ------------------------------------------


class SelectingIMAP(FakeIMAP):
    """Tracks folder selection the way a server does, including failed SELECTs."""

    def __init__(self, *args, unselectable=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.unselectable = set(unselectable)
        self.selected = None

    def login(self, user, password):
        pass

    def noop(self):
        pass  # succeeds even with no mailbox selected

    def logout(self):
        pass

    def select_folder(self, name, readonly=True):
        self.selected = None
        if name in self.unselectable:
            raise RuntimeError("NO [NONEXISTENT] Mailbox doesn't exist")
        self.selected = name

    def _require_selection(self):
        if self.selected is None:
            raise RuntimeError("No mailbox selected")

    def search(self, criteria):
        self._require_selection()
        return super().search(criteria)

    def fetch(self, uids, fields):
        self._require_selection()
        return super().fetch(uids, fields)


def test_a_broken_sent_folder_does_not_wedge_the_connection(monkeypatch, imap):
    """A misconfigured sent_folder must not leave the connection unusable."""
    from emailmcp.imap import connection as connection_module

    server = SelectingIMAP(imap.metadata, imap.raw, imap.threads_result, unselectable={"Nope"})
    monkeypatch.setattr(
        connection_module.imapclient,
        "IMAPClient",
        lambda host, port=None, ssl=True: server,
    )

    store = MailStore(Mailbox(key="andrsk.cz", address=ADDRESS, sent_folder="Nope"), "password")

    # Every probe of the bogus Sent folder fails and is swallowed...
    rows = store.list_inbox()
    assert [row["id"] for row in rows] == ["andrsk.cz:1", "andrsk.cz:2"]
    assert all(row["needs_reply"] for row in rows)

    # ...and the connection still works for everything afterwards.
    assert store.read_email(1).metadata["subject"] == "Nabidka"
    assert len(store.list_inbox()) == 2
