import pytest

from emailmcp.config import Mailbox
from emailmcp.refs import InvalidMessageRef
from emailmcp.registry import Registry, UnknownMailbox

ENV = {
    "MAILBOX_ANDRSK_CZ_PASSWORD": "one",
    "MAILBOX_SECOND_EXAMPLE_PASSWORD": "two",
}

MAILBOXES = [
    Mailbox(key="andrsk.cz", address="me@andrsk.cz"),
    Mailbox(key="second.example", address="me@second.example"),
]


@pytest.fixture
def registry():
    return Registry(MAILBOXES, ENV)


def test_keys_and_length(registry):
    assert registry.keys == ["andrsk.cz", "second.example"]
    assert len(registry) == 2


def test_get_returns_the_matching_store(registry):
    assert registry.get("second.example").address == "me@second.example"


def test_get_tolerates_surrounding_whitespace(registry):
    assert registry.get("  andrsk.cz ").key == "andrsk.cz"


def test_unknown_mailbox_lists_the_valid_keys(registry):
    with pytest.raises(UnknownMailbox) as excinfo:
        registry.get("work")
    assert "andrsk.cz, second.example" in str(excinfo.value)


def test_resolve_routes_to_the_mailbox_named_in_the_id(registry):
    store, uid = registry.resolve("second.example:4211")
    assert store.key == "second.example"
    assert uid == 4211


def test_a_uid_cannot_be_used_without_its_mailbox(registry):
    with pytest.raises(InvalidMessageRef):
        registry.resolve("4211")


def test_each_mailbox_gets_its_own_password(registry):
    stores = list(registry)
    assert stores[0]._conn._password == "one"
    assert stores[1]._conn._password == "two"


def test_stores_share_no_state(registry):
    first, second = list(registry)
    first._sent_reply_cache["<a@b>"] = True
    assert second._sent_reply_cache == {}
    assert first._conn is not second._conn


def test_describe_exposes_the_id_prefix(registry):
    described = registry.describe()
    assert described[0]["message_id_prefix"] == "andrsk.cz:"
    assert described[0]["address"] == "me@andrsk.cz"


def test_a_missing_password_fails_at_construction():
    from emailmcp.config import MailboxConfigError

    with pytest.raises(MailboxConfigError):
        Registry(MAILBOXES, {})
