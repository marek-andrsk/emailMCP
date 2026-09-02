"""IMAP fakes shared by the store and tool tests."""

from contextlib import contextmanager

from emailmcp.config import Mailbox
from emailmcp.imap.store import MailStore

ADDRESS = "me@andrsk.cz"


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
