import pytest

from emailmcp.imap import connection as connection_module
from emailmcp.imap.connection import Connection


class FakeIMAP:
    def __init__(self, fail_noop=False, unselectable=()):
        self.selects = []
        self.noops = 0
        self.logged_out = False
        self.fail_noop = fail_noop
        self.unselectable = set(unselectable)

    def login(self, user, password):
        pass

    def noop(self):
        self.noops += 1
        if self.fail_noop:
            raise OSError("connection dropped")

    def select_folder(self, name, readonly=True):
        if name in self.unselectable:
            # A failed SELECT leaves no mailbox selected (RFC 3501).
            raise RuntimeError("NO [NONEXISTENT] Mailbox doesn't exist")
        self.selects.append((name, readonly))

    def logout(self):
        self.logged_out = True


def patch_imap(monkeypatch, queue):
    """Replace the imapclient constructor so the real _connect() still runs."""
    monkeypatch.setattr(
        connection_module.imapclient,
        "IMAPClient",
        lambda host, port=None, ssl=True: queue.pop(0),
    )


def connect(monkeypatch, *fakes) -> Connection:
    patch_imap(monkeypatch, list(fakes))
    return Connection("host", 993, "user", "pass")


def test_selection_is_cached_and_restored_around_nesting(monkeypatch):
    imap = FakeIMAP()
    conn = connect(monkeypatch, imap)

    with conn.folder("INBOX"):
        pass
    with conn.folder("INBOX"):  # cached, no second SELECT
        with conn.folder("Sent"):
            with conn.session():
                pass
    with conn.folder("INBOX", readonly=False):  # a mode change is a real change
        pass

    assert imap.selects == [
        ("INBOX", True),
        ("Sent", True),
        ("INBOX", True),  # the inner block restored the enclosing selection
        ("INBOX", False),
    ]
    # One liveness probe for the outermost acquisition of each top-level block.
    assert imap.noops <= 3
    conn.close()


def test_a_dead_connection_or_a_failed_select_never_leaves_a_stale_cache(monkeypatch):
    """Trusting a cache the server does not share is how commands end up
    running against whatever folder happened to be selected."""
    dead, fresh = FakeIMAP(fail_noop=True), FakeIMAP()
    conn = connect(monkeypatch, dead, fresh)

    with conn.folder("INBOX"):
        pass
    with conn.folder("INBOX"):
        pass
    assert dead.logged_out
    assert fresh.selects == [("INBOX", True)]  # reselected, not assumed

    imap = FakeIMAP(unselectable={"Sent"})
    conn = connect(monkeypatch, imap)
    with conn.folder("INBOX"):
        # Swallowed exactly as _has_sent_reply does for a missing Sent folder.
        with pytest.raises(RuntimeError):
            with conn.folder("Sent"):
                pass
        assert conn._selected == ("INBOX", True)
    # The restore left a cache the server agrees with, so this one is free.
    with conn.folder("INBOX"):
        pass
    assert imap.selects == [("INBOX", True), ("INBOX", True)]
