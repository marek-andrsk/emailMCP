import pytest

from emailmcp.config import Mailbox, MailboxConfigError
from emailmcp.refs import InvalidMessageRef
from emailmcp.registry import Registry, UnknownMailbox

ENV = {"MAILBOX_ANDRSK_CZ_PASSWORD": "one", "MAILBOX_SECOND_EXAMPLE_PASSWORD": "two"}
MAILBOXES = [
    Mailbox(key="andrsk.cz", address="me@andrsk.cz"),
    Mailbox(key="second.example", address="me@second.example"),
]


def test_lookups_route_to_the_named_mailbox_and_nowhere_else():
    registry = Registry(MAILBOXES, ENV)
    assert registry.keys == ["andrsk.cz", "second.example"]
    assert registry.get("  andrsk.cz ")._conn._password == "one"

    store, uid = registry.resolve("second.example:4211")
    assert (store.key, uid) == ("second.example", 4211)
    # Stores share nothing, so one mailbox's cache cannot answer for another.
    assert registry.get("andrsk.cz")._conn is not store._conn

    with pytest.raises(UnknownMailbox) as excinfo:
        registry.get("work")
    assert "andrsk.cz, second.example" in str(excinfo.value)
    with pytest.raises(InvalidMessageRef):
        registry.resolve("4211")


def test_a_missing_password_fails_at_construction_not_on_first_use():
    with pytest.raises(MailboxConfigError):
        Registry(MAILBOXES, {})
