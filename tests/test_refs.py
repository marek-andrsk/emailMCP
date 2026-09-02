import pytest

from emailmcp.refs import InvalidMessageRef, MessageRef, format_ref, parse_ref

KEYS = ["andrsk.cz", "second.example"]


def test_a_ref_round_trips_through_whitespace_and_dotted_keys():
    assert format_ref("andrsk.cz", 4211) == "andrsk.cz:4211"
    assert parse_ref("  andrsk.cz:4211 ", KEYS) == MessageRef("andrsk.cz", 4211)
    # The key itself contains dots, so the split has to be on the last one.
    assert parse_ref("second.example:7", KEYS) == MessageRef("second.example", 7)


def test_a_ref_that_could_address_the_wrong_mailbox_is_refused():
    """A UID paired with the wrong key resolves to a real, different message —
    in the worst case a reply addressed to someone else. Every rejection names
    the valid keys, which is the loop a model corrects itself through."""
    for raw, expected in [
        ("4211", "missing its mailbox"),
        ("work:4211", "andrsk.cz, second.example"),
        ("andrsk.cz:abc", "non-numeric"),
        ("   ", "empty message id"),
    ]:
        with pytest.raises(InvalidMessageRef) as excinfo:
            parse_ref(raw, KEYS)
        assert expected in str(excinfo.value), raw
