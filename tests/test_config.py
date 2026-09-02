import json

import pytest

from emailmcp.config import Mailbox, MailboxConfigError, load_mailboxes, password_for

ENTRY = {"key": "andrsk.cz", "address": "me@andrsk.cz", "host": "imap.migadu.com"}


def env_with(entries, **extra):
    return {"MAILBOXES_JSON": json.dumps(entries), **extra}


def error(env) -> str:
    with pytest.raises(MailboxConfigError) as excinfo:
        load_mailboxes(env)
    return str(excinfo.value)


def test_a_valid_configuration_loads_with_its_defaults_and_passwords():
    env = env_with(
        [ENTRY, {"key": "second.example", "address": "me@second.example", "label": "Work"}],
        MAILBOX_ANDRSK_CZ_PASSWORD="one",
        MAILBOX_SECOND_EXAMPLE_PASSWORD="two",
    )
    first, second = load_mailboxes(env)

    assert (first.port, first.inbox_folder, first.drafts_folder, first.sent_folder) == (
        993,
        "INBOX",
        "Drafts",
        None,
    )
    assert first.display == "me@andrsk.cz"
    assert second.display == "Work"
    assert first.password_env == "MAILBOX_ANDRSK_CZ_PASSWORD"
    assert password_for(first, env) == "one"


def test_every_configuration_problem_is_reported_at_once():
    """MAILBOXES_JSON is the only source, so a bad value has to say everything
    that is wrong with it — there is no second place to look."""
    message = error(
        env_with(
            [
                {"key": "ok.cz", "address": "me@ok.cz"},
                {"key": "BAD KEY", "address": "me@bad.cz"},
                {"key": "no-address.cz"},
            ]
        )
    )
    assert "entry #1" in message and "entry #2" in message
    assert "MAILBOX_OK_CZ_PASSWORD" in message
    assert "invalid JSON" in error({"MAILBOXES_JSON": "{not json"})


def test_configurations_that_would_silently_misroute_are_refused():
    # Two keys collapsing onto one password variable: the second mailbox would
    # otherwise authenticate with the first one's password.
    assert "shares MAILBOX_ANDRSK_CZ_PASSWORD" in error(
        env_with(
            [
                {"key": "andrsk.cz", "address": "me@andrsk.cz"},
                {"key": "andrsk-cz", "address": "me@andrsk.eu"},
            ],
            MAILBOX_ANDRSK_CZ_PASSWORD="secret",
        )
    )
    assert "duplicate key" in error(env_with([ENTRY, ENTRY], MAILBOX_ANDRSK_CZ_PASSWORD="s"))
    # A key carrying the id separator would make every message id ambiguous.
    with pytest.raises(ValueError):
        Mailbox(key="andrsk:cz", address="me@andrsk.cz")
