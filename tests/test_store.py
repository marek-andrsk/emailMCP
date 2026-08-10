import email
import email.policy
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from emailmcp.config import Mailbox
from emailmcp.imap.store import MailStore, MessageNotFound

ADDRESS = "me@andrsk.cz"

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

plain
--x
Content-Type: text/html; charset="utf-8"

<p>rich</p>
--x--
""".encode()


class Addr:
    def __init__(self, name, mailbox, host):
        self.name = name
        self.mailbox = mailbox
        self.host = host


class Envelope:
    def __init__(self, sender, subject, message_id):
        self.from_ = [sender]
        self.subject = subject
        self.message_id = message_id
        self.date = None


def meta(sender_name, sender_addr, subject, message_id, when, flags=(), body=b"snippet text"):
    mailbox, _, host = sender_addr.partition("@")
    return {
        b"ENVELOPE": Envelope(
            Addr(sender_name.encode(), mailbox.encode(), host.encode()),
            subject.encode(),
            message_id.encode(),
        ),
        b"FLAGS": list(flags),
        b"INTERNALDATE": when,
        b"BODY[TEXT]<0>": body,
    }


class FakeIMAP:
    def __init__(self, metadata, raw, threads, sent_hits=()):
        self.metadata = metadata
        self.raw = raw
        self.threads_result = threads
        self.sent_hits = set(sent_hits)
        self.appended = []
        self.searches = []

    def search(self, criteria):
        self.searches.append(criteria)
        if criteria == ["ALL"]:
            return sorted(self.metadata)
        # Sent-folder probe: ["OR", "HEADER", "In-Reply-To", <id>, ...]
        return [1] if criteria[3] in self.sent_hits else []

    def fetch(self, uids, fields):
        if "RFC822" in fields:
            return {uid: {b"RFC822": self.raw[uid]} for uid in uids if uid in self.raw}
        return {uid: self.metadata[uid] for uid in uids if uid in self.metadata}

    def thread(self, algorithm, criteria):
        return self.threads_result

    def list_folders(self):
        return [((b"\\Sent",), b"/", "Sent"), ((), b"/", "Drafts"), ((), b"/", "INBOX")]

    def append(self, folder, message, flags=None, msg_time=None):
        self.appended.append((folder, message, flags))


class FakeConnection:
    def __init__(self, imap):
        self.imap = imap
        self.folders = []

    @contextmanager
    def folder(self, name, readonly=True):
        self.folders.append(name)
        yield self.imap

    @contextmanager
    def session(self):
        yield self.imap

    def close(self):
        pass


def build_store(imap, **overrides):
    mailbox = Mailbox(key="andrsk.cz", address=ADDRESS, label="Marek", **overrides)
    store = MailStore(mailbox, "password")
    store._conn = FakeConnection(imap)
    return store


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


# --- list_inbox ------------------------------------------------------------


def test_list_inbox_returns_prefixed_ids(imap):
    rows = build_store(imap).list_inbox()
    assert [row["id"] for row in rows] == ["andrsk.cz:1", "andrsk.cz:2"]
    assert all(row["mailbox"] == "andrsk.cz" for row in rows)
    assert rows[0]["thread_ids"] == ["andrsk.cz:1"]


def test_list_inbox_shapes_metadata(imap):
    row = build_store(imap).list_inbox()[0]
    assert row["from"] == "Alice <alice@example.com>"
    assert row["subject"] == "Nabidka"
    assert row["date"] == "2026-08-10 09:00"
    assert row["snippet"] == "snippet text"
    assert "uid" not in row  # raw UIDs are never exposed


def test_latest_message_represents_a_thread(imap):
    imap.threads_result = [[1, 2]]
    rows = build_store(imap).list_inbox()
    assert len(rows) == 1
    assert rows[0]["id"] == "andrsk.cz:2"
    assert rows[0]["thread_ids"] == ["andrsk.cz:1", "andrsk.cz:2"]


def test_empty_inbox(imap):
    imap.metadata = {}
    assert build_store(imap).list_inbox() == []


# --- needs_reply -----------------------------------------------------------


def test_incoming_unanswered_mail_needs_a_reply(imap):
    assert build_store(imap).list_inbox()[0]["needs_reply"] is True


def test_own_mail_never_needs_a_reply(imap):
    imap.metadata[1] = meta(
        "Marek", ADDRESS, "Nabidka", "<a1@example.com>", datetime(2026, 8, 10, tzinfo=timezone.utc)
    )
    assert build_store(imap).list_inbox()[0]["needs_reply"] is False


def test_answered_flag_clears_needs_reply(imap):
    imap.metadata[1][b"FLAGS"] = [b"\\Answered"]
    assert build_store(imap).list_inbox()[0]["needs_reply"] is False


def test_a_matching_message_in_sent_clears_needs_reply(imap):
    imap.sent_hits = {"<a1@example.com>"}
    rows = build_store(imap).list_inbox()
    assert rows[0]["needs_reply"] is False
    assert rows[1]["needs_reply"] is True


def test_the_sent_probe_is_cached_per_message(imap):
    store = build_store(imap)
    store.list_inbox()
    store.list_inbox()
    sent_probes = [c for c in imap.searches if c != ["ALL"]]
    assert len(sent_probes) == 2  # two distinct message ids, each probed once


# --- read_email ------------------------------------------------------------


def test_read_email_shape(imap):
    result = build_store(imap).read_email(1)
    assert result["id"] == "andrsk.cz:1"
    assert result["mailbox"] == "andrsk.cz"
    assert result["from"] == "Alice <alice@example.com>"
    assert result["subject"] == "Nabidka"
    assert result["body"] == "Ahoj, mam pro tebe nabidku."
    assert result["message_id"] == "<a1@example.com>"
    assert result["thread_ids"] == ["andrsk.cz:1"]
    assert result["thread_context"][0]["id"] == "andrsk.cz:1"


def test_thread_context_is_ordered_oldest_first(imap):
    imap.threads_result = [[1, 2]]
    result = build_store(imap).read_email(2)
    assert [entry["id"] for entry in result["thread_context"]] == ["andrsk.cz:1", "andrsk.cz:2"]


def test_missing_message_names_the_reference(imap):
    with pytest.raises(MessageNotFound) as excinfo:
        build_store(imap).read_email(99)
    assert "andrsk.cz:99" in str(excinfo.value)


# --- drafts ----------------------------------------------------------------


def parse_appended(imap, index=0):
    folder, raw, flags = imap.appended[index]
    return folder, email.message_from_bytes(raw, policy=email.policy.default), flags


def test_reply_draft_headers(imap):
    store = build_store(imap)
    store.save_reply_draft(1, "Diky, ozvu se.")
    folder, draft, flags = parse_appended(imap)

    assert folder == "Drafts"
    assert flags == [b"\\Draft"]
    assert draft["From"] == ADDRESS
    assert draft["To"] == "Alice <alice@example.com>"
    assert draft["Subject"] == "Re: Nabidka"
    assert draft["In-Reply-To"] == "<a1@example.com>"
    assert draft["References"] == "<a1@example.com>"


def test_reply_draft_extends_an_existing_references_chain(imap):
    build_store(imap).save_reply_draft(2, "ok")
    _folder, draft, _flags = parse_appended(imap)
    assert draft["References"] == "<b0@example.com> <b1@example.com>"
    assert draft["Subject"] == "Re: Faktura"  # no double Re:


def test_reply_draft_quotes_the_original(imap):
    build_store(imap).save_reply_draft(1, "Diky, ozvu se.")
    _folder, draft, _flags = parse_appended(imap)
    body = draft.get_payload(decode=True).decode("utf-8")
    assert body.startswith("Diky, ozvu se.")
    assert "> Ahoj, mam pro tebe nabidku." in body


def test_reply_to_html_mail_produces_multipart_alternative(imap):
    build_store(imap).save_reply_draft(2, "ok")
    _folder, draft, _flags = parse_appended(imap)
    assert draft.get_content_type() == "multipart/alternative"
    types = [part.get_content_type() for part in draft.walk()]
    assert "text/plain" in types and "text/html" in types


BLANK_PLAIN_WITH_HTML = f"""From: Carol <carol@example.com>
To: {ADDRESS}
Date: Mon, 10 Aug 2026 11:00:00 +0200
Subject: Newsletter
Message-ID: <c1@example.com>
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="y"

--y
Content-Type: text/plain; charset="utf-8"

\t
--y
Content-Type: text/html; charset="utf-8"

<p>The real content of the message</p>
--y--
""".encode()


def test_a_reply_to_a_blank_plain_part_keeps_the_quote_in_both_halves():
    """A whitespace-only text/plain part must not empty the quoted thread."""
    when = datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc)
    metadata = {3: meta("Carol", "carol@example.com", "Newsletter", "<c1@example.com>", when)}
    imap = FakeIMAP(metadata, {3: BLANK_PLAIN_WITH_HTML}, threads=((3,),))

    build_store(imap).save_reply_draft(3, "Diky, nezajem.")
    _folder, draft, _flags = parse_appended(imap)

    plain = next(p for p in draft.walk() if p.get_content_type() == "text/plain")
    text = plain.get_payload(decode=True).decode("utf-8")
    assert text.startswith("Diky, nezajem.")
    assert "> The real content of the message" in text


def test_reply_to_plain_mail_stays_plain(imap):
    build_store(imap).save_reply_draft(1, "ok")
    _folder, draft, _flags = parse_appended(imap)
    assert draft.get_content_type() == "text/plain"


def test_identical_reply_saved_twice_is_suppressed(imap):
    store = build_store(imap)
    store.save_reply_draft(1, "ok")
    message = store.save_reply_draft(1, "ok")
    assert len(imap.appended) == 1
    assert "duplicate suppressed" in message


def test_the_dedupe_check_and_the_append_are_one_critical_section(imap):
    """Testing before recording would let two overlapping retries both through."""
    store = build_store(imap)
    held = []
    original_append = imap.append

    def watching(folder, message, flags=None, msg_time=None):
        held.append(store._draft_lock.locked())
        original_append(folder, message, flags=flags, msg_time=msg_time)

    imap.append = watching
    store.save_reply_draft(1, "ok")
    assert held == [True]


def test_a_retry_arriving_mid_save_does_not_produce_a_second_draft(imap):
    """The second caller is held off until the first has recorded its signature."""
    import threading

    store = build_store(imap)
    inside_append = threading.Event()
    another_reached_append = threading.Event()
    original_append = imap.append
    seen = []

    def instrumented(folder, message, flags=None, msg_time=None):
        first = not seen
        seen.append(True)
        if first:
            inside_append.set()
            # Hold the window open long enough for a second caller to slip past
            # an unguarded check; with the lock it never gets here.
            another_reached_append.wait(timeout=0.3)
        else:
            another_reached_append.set()
        original_append(folder, message, flags=flags, msg_time=msg_time)

    imap.append = instrumented
    errors = []

    def save():
        try:
            store.save_reply_draft(1, "ok")
        except Exception as exc:
            errors.append(repr(exc))

    first_thread = threading.Thread(target=save)
    first_thread.start()
    assert inside_append.wait(timeout=2.0), "first save never reached APPEND"

    second_thread = threading.Thread(target=save)
    second_thread.start()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not errors, errors
    assert len(imap.appended) == 1


def test_a_different_body_is_not_suppressed(imap):
    store = build_store(imap)
    store.save_reply_draft(1, "ok")
    store.save_reply_draft(1, "something else")
    assert len(imap.appended) == 2


def test_a_failed_append_can_be_retried_immediately(imap):
    store = build_store(imap)
    calls = {"n": 0}

    def flaky(folder, message, flags=None, msg_time=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("append failed")
        imap.appended.append((folder, message, flags))

    imap.append = flaky
    with pytest.raises(OSError):
        store.save_reply_draft(1, "ok")
    store.save_reply_draft(1, "ok")
    assert len(imap.appended) == 1


def test_new_draft_is_sent_from_the_mailbox_address(imap):
    message = build_store(imap).save_new_draft("someone@example.com", "Dotaz", "Text")
    _folder, draft, _flags = parse_appended(imap)
    assert draft["From"] == ADDRESS
    assert draft["To"] == "someone@example.com"
    assert draft["Subject"] == "Dotaz"
    assert "Marek" in message  # the label tells the user where it landed


def test_configured_drafts_folder_is_used(imap):
    build_store(imap, drafts_folder="INBOX.Drafts").save_new_draft("a@b.cz", "s", "b")
    assert imap.appended[0][0] == "INBOX.Drafts"


def test_drafts_folder_is_discovered_when_unset(imap):
    build_store(imap, drafts_folder=None).save_new_draft("a@b.cz", "s", "b")
    assert imap.appended[0][0] == "Drafts"


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

    mailbox = Mailbox(key="andrsk.cz", address=ADDRESS, sent_folder="Nope")
    store = MailStore(mailbox, "password")

    # Every probe of the bogus Sent folder fails and is swallowed...
    rows = store.list_inbox()
    assert [row["id"] for row in rows] == ["andrsk.cz:1", "andrsk.cz:2"]
    assert all(row["needs_reply"] for row in rows)

    # ...and the connection still works for everything afterwards.
    assert store.read_email(1)["subject"] == "Nabidka"
    assert len(store.list_inbox()) == 2
