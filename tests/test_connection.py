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


@pytest.fixture
def conn(monkeypatch):
    patch_imap(monkeypatch, [FakeIMAP()])
    return Connection("host", 993, "user", "pass")


def test_repeated_selection_of_the_same_folder_issues_one_select(conn):
    with conn.folder("INBOX") as imap:
        pass
    with conn.folder("INBOX"):
        pass
    assert imap.selects == [("INBOX", True)]


def test_readonly_change_forces_a_reselect(conn):
    with conn.folder("INBOX", readonly=True) as imap:
        pass
    with conn.folder("INBOX", readonly=False):
        pass
    assert imap.selects == [("INBOX", True), ("INBOX", False)]


def test_nested_folder_restores_the_outer_selection(conn):
    with conn.folder("INBOX") as imap:
        with conn.folder("Sent"):
            pass
        # Back on INBOX before the outer block continues.
        assert imap.selects == [("INBOX", True), ("Sent", True), ("INBOX", True)]


def test_nested_access_does_not_reprobe_the_connection(conn):
    with conn.folder("INBOX") as imap:
        with conn.folder("Sent"):
            with conn.session():
                pass
    # One liveness probe for the outermost acquisition only.
    assert imap.noops <= 1


def test_a_dead_connection_is_replaced_and_the_selection_cache_reset(monkeypatch):
    dead = FakeIMAP(fail_noop=True)
    fresh = FakeIMAP()
    patch_imap(monkeypatch, [dead, fresh])
    connection = Connection("host", 993, "user", "pass")

    with connection.folder("INBOX"):
        pass
    assert dead.selects == [("INBOX", True)]

    with connection.folder("INBOX"):
        pass
    # Reconnected, and INBOX was selected again rather than assumed.
    assert dead.logged_out
    assert fresh.selects == [("INBOX", True)]


def test_a_failed_select_does_not_leave_a_stale_cache(monkeypatch):
    imap = FakeIMAP(unselectable={"Sent"})
    patch_imap(monkeypatch, [imap])
    connection = Connection("host", 993, "user", "pass")

    with connection.folder("INBOX"):
        pass
    with pytest.raises(RuntimeError):
        with connection.folder("Sent"):
            pass

    assert connection._selected is None

    # The next call must issue a real SELECT rather than trust a cache that
    # still named INBOX while the server had nothing selected.
    with connection.folder("INBOX"):
        pass
    assert imap.selects == [("INBOX", True), ("INBOX", True)]


def test_a_failed_nested_select_restores_the_enclosing_folder(monkeypatch):
    imap = FakeIMAP(unselectable={"Sent"})
    patch_imap(monkeypatch, [imap])
    connection = Connection("host", 993, "user", "pass")

    with connection.folder("INBOX"):
        # Swallowed exactly as _has_sent_reply does for a missing Sent folder.
        try:
            with connection.folder("Sent"):
                pass
        except RuntimeError:
            pass
        # The outer block is genuinely back on INBOX, not just believed to be.
        assert connection._selected == ("INBOX", True)

    assert imap.selects == [("INBOX", True), ("INBOX", True)]


def test_close_is_safe_when_never_connected(conn):
    conn.close()
    assert conn._conn is None
