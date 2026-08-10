import json

import pytest

from emailmcp.config import Mailbox, MailboxConfigError, load_mailboxes, password_for

ENTRY = {"key": "andrsk.cz", "address": "me@andrsk.cz", "host": "imap.migadu.com"}


def env_with(entries, **extra):
    env = {"MAILBOXES_JSON": json.dumps(entries)}
    env.update(extra)
    return env


def test_password_env_name_is_derived_from_the_key():
    assert Mailbox(**ENTRY).password_env == "MAILBOX_ANDRSK_CZ_PASSWORD"


def test_defaults_are_applied():
    mailbox = Mailbox(**ENTRY)
    assert mailbox.port == 993
    assert mailbox.inbox_folder == "INBOX"
    assert mailbox.drafts_folder == "Drafts"
    assert mailbox.sent_folder is None
    assert mailbox.display == "me@andrsk.cz"


def test_label_wins_for_display():
    assert Mailbox(**ENTRY, label="Work").display == "Work"


def test_loads_from_inline_json():
    env = env_with([ENTRY], MAILBOX_ANDRSK_CZ_PASSWORD="secret")
    mailboxes = load_mailboxes(env)
    assert [m.key for m in mailboxes] == ["andrsk.cz"]
    assert password_for(mailboxes[0], env) == "secret"


def test_a_multiline_value_is_accepted():
    raw = """[
      {"key": "andrsk.cz", "address": "me@andrsk.cz"},
      {"key": "second.cz", "address": "me@second.cz"}
    ]"""
    env = {
        "MAILBOXES_JSON": raw,
        "MAILBOX_ANDRSK_CZ_PASSWORD": "one",
        "MAILBOX_SECOND_CZ_PASSWORD": "two",
    }
    assert [m.key for m in load_mailboxes(env)] == ["andrsk.cz", "second.cz"]


def test_no_other_source_is_consulted(tmp_path, monkeypatch):
    """MAILBOXES_JSON is the only source — no file fallback to reason about."""
    path = tmp_path / "mailboxes.json"
    path.write_text(json.dumps([{**ENTRY, "key": "from.file"}]), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(MailboxConfigError) as excinfo:
        load_mailboxes({"MAILBOXES_CONFIG": str(path)})
    assert "set MAILBOXES_JSON" in str(excinfo.value)


def test_a_key_containing_the_separator_is_rejected():
    with pytest.raises(ValueError):
        Mailbox(key="andrsk:cz", address="me@andrsk.cz")


def test_uppercase_key_is_rejected():
    with pytest.raises(ValueError):
        Mailbox(key="Andrsk.cz", address="me@andrsk.cz")


def test_unknown_field_is_rejected():
    with pytest.raises(ValueError):
        Mailbox(**ENTRY, imap_pass="oops")


def test_missing_password_is_reported_with_the_variable_name():
    with pytest.raises(MailboxConfigError) as excinfo:
        load_mailboxes(env_with([ENTRY]))
    assert "MAILBOX_ANDRSK_CZ_PASSWORD" in str(excinfo.value)


def test_duplicate_keys_are_reported():
    env = env_with([ENTRY, ENTRY], MAILBOX_ANDRSK_CZ_PASSWORD="secret")
    with pytest.raises(MailboxConfigError) as excinfo:
        load_mailboxes(env)
    assert "duplicate key" in str(excinfo.value)


def test_every_problem_is_reported_at_once():
    entries = [
        {"key": "ok.cz", "address": "me@ok.cz"},
        {"key": "BAD KEY", "address": "me@bad.cz"},
        {"key": "no-address.cz"},
    ]
    with pytest.raises(MailboxConfigError) as excinfo:
        load_mailboxes(env_with(entries))
    message = str(excinfo.value)
    assert "entry #1" in message
    assert "entry #2" in message
    assert "MAILBOX_OK_CZ_PASSWORD" in message


def test_duplicate_indices_survive_an_earlier_invalid_entry():
    entries = [
        {"key": "BAD KEY", "address": "me@bad.cz"},
        {"key": "ok.cz", "address": "me@ok.cz"},
        {"key": "ok.cz", "address": "me@second.cz"},
    ]
    with pytest.raises(MailboxConfigError) as excinfo:
        load_mailboxes(env_with(entries, MAILBOX_OK_CZ_PASSWORD="secret"))
    # Indices name the JSON array, not the list of entries that validated.
    assert "entry #2: duplicate key 'ok.cz' (first seen at entry #1)" in str(excinfo.value)


def test_keys_differing_only_in_punctuation_collide_on_one_password_variable():
    entries = [
        {"key": "andrsk.cz", "address": "me@andrsk.cz"},
        {"key": "andrsk-cz", "address": "me@andrsk.eu"},
    ]
    with pytest.raises(MailboxConfigError) as excinfo:
        load_mailboxes(env_with(entries, MAILBOX_ANDRSK_CZ_PASSWORD="secret"))
    message = str(excinfo.value)
    assert "shares MAILBOX_ANDRSK_CZ_PASSWORD" in message
    assert "'andrsk.cz'" in message


def test_distinct_keys_with_distinct_variables_are_fine():
    entries = [
        {"key": "andrsk.cz", "address": "me@andrsk.cz"},
        {"key": "second.example", "address": "me@second.example"},
    ]
    env = env_with(
        entries,
        MAILBOX_ANDRSK_CZ_PASSWORD="one",
        MAILBOX_SECOND_EXAMPLE_PASSWORD="two",
    )
    assert [m.key for m in load_mailboxes(env)] == ["andrsk.cz", "second.example"]


def test_invalid_json_is_reported():
    with pytest.raises(MailboxConfigError) as excinfo:
        load_mailboxes({"MAILBOXES_JSON": "{not json"})
    assert "invalid JSON" in str(excinfo.value)


def test_empty_list_is_rejected():
    with pytest.raises(MailboxConfigError):
        load_mailboxes({"MAILBOXES_JSON": "[]"})


def test_missing_configuration_shows_the_expected_shape():
    with pytest.raises(MailboxConfigError) as excinfo:
        load_mailboxes({})
    message = str(excinfo.value)
    assert "set MAILBOXES_JSON" in message
    assert '"key":"example.com"' in message


def test_a_blank_value_counts_as_missing():
    with pytest.raises(MailboxConfigError) as excinfo:
        load_mailboxes({"MAILBOXES_JSON": "   "})
    assert "set MAILBOXES_JSON" in str(excinfo.value)
