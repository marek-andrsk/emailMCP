import pytest

from emailmcp.refs import InvalidMessageRef, MessageRef, format_ref, parse_ref

KEYS = ["andrsk.cz", "second.example"]


def test_format_and_roundtrip():
    assert format_ref("andrsk.cz", 4211) == "andrsk.cz:4211"
    assert parse_ref("andrsk.cz:4211", KEYS) == MessageRef("andrsk.cz", 4211)


def test_whitespace_is_tolerated():
    assert parse_ref("  andrsk.cz:4211 ", KEYS).uid == 4211


def test_bare_uid_is_rejected_and_suggests_a_mailbox():
    with pytest.raises(InvalidMessageRef) as excinfo:
        parse_ref("4211", KEYS)
    message = str(excinfo.value)
    assert "missing its mailbox" in message
    assert "andrsk.cz:4211" in message


def test_unknown_mailbox_lists_valid_keys():
    with pytest.raises(InvalidMessageRef) as excinfo:
        parse_ref("work:4211", KEYS)
    assert "andrsk.cz, second.example" in str(excinfo.value)


def test_non_numeric_uid_is_rejected():
    with pytest.raises(InvalidMessageRef):
        parse_ref("andrsk.cz:abc", KEYS)


def test_empty_is_rejected():
    with pytest.raises(InvalidMessageRef):
        parse_ref("   ", KEYS)


def test_key_containing_dots_splits_on_the_last_separator():
    assert parse_ref("second.example:7", KEYS) == MessageRef("second.example", 7)
