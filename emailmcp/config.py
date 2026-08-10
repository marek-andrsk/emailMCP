"""Mailbox configuration.

Mailboxes are defined by ``MAILBOXES_JSON``, a JSON array of non-secret mailbox
objects. That is the only source: one variable, no file fallback and no
precedence rules to reason about. It reads from ``.env`` locally (dotenv accepts
a quoted multi-line value) and from the platform's environment in production.

Passwords are never part of that JSON. Every mailbox needs its own in
``MAILBOX_<KEY>_PASSWORD``, where ``<KEY>`` is the mailbox key uppercased with
non-alphanumeric characters replaced by ``_`` — so ``andrsk.cz`` looks for
``MAILBOX_ANDRSK_CZ_PASSWORD``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Mapping

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .refs import SEPARATOR

# Keys appear in message references ("<key>:<uid>"), so the separator and
# whitespace are excluded by construction.
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

CONFIG_ENV = "MAILBOXES_JSON"

EXAMPLE = (
    """MAILBOXES_JSON='[{"key":"example.com","address":"me@example.com",""" """"host":"imap.migadu.com"}]'"""
)


class MailboxConfigError(RuntimeError):
    """Raised at startup when the mailbox configuration cannot be used."""


class Mailbox(BaseModel):
    """Non-secret definition of a single IMAP mailbox."""

    model_config = ConfigDict(extra="forbid")

    key: str
    address: str
    label: str | None = None
    host: str = "imap.migadu.com"
    port: int = 993
    inbox_folder: str = "INBOX"
    # ``None`` means "discover via the IMAP special-use flag".
    drafts_folder: str | None = "Drafts"
    sent_folder: str | None = None

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        if not KEY_PATTERN.match(value):
            raise ValueError(
                f"invalid key {value!r}: use lowercase letters, digits, '.', '-' or '_' "
                f"and do not include {SEPARATOR!r} (it separates key from UID in message ids)"
            )
        return value

    @field_validator("address")
    @classmethod
    def _validate_address(cls, value: str) -> str:
        if "@" not in value.strip("@"):
            raise ValueError(f"invalid address {value!r}: expected an email address")
        return value.strip()

    @property
    def display(self) -> str:
        return self.label or self.address

    @property
    def password_env(self) -> str:
        return f"MAILBOX_{re.sub(r'[^A-Z0-9]', '_', self.key.upper())}_PASSWORD"


def password_for(mailbox: Mailbox, env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    password = env.get(mailbox.password_env, "")
    if not password:
        raise MailboxConfigError(
            f"missing password for mailbox {mailbox.key!r}: set {mailbox.password_env}"
        )
    return password


def _read_raw(env: Mapping[str, str]) -> str:
    raw = env.get(CONFIG_ENV, "").strip()
    if not raw:
        raise MailboxConfigError(
            f"no mailbox configuration: set {CONFIG_ENV} to a JSON array of mailbox "
            f"objects, for example\n  {EXAMPLE}"
        )
    return raw


def _format_validation_error(index: int, error: ValidationError) -> list[str]:
    problems = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"]) or "(entry)"
        problems.append(f"  entry #{index}: {location}: {detail['msg']}")
    return problems


def load_mailboxes(env: Mapping[str, str] | None = None) -> list[Mailbox]:
    """Load and fully validate the mailbox list.

    Every problem found is reported at once so a misconfigured deploy fails at
    startup with a complete list rather than one error per restart.
    """
    env = os.environ if env is None else env
    raw = _read_raw(env)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MailboxConfigError(f"{CONFIG_ENV}: invalid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise MailboxConfigError(f"{CONFIG_ENV}: expected a JSON array of mailbox objects")
    if not parsed:
        raise MailboxConfigError(f"{CONFIG_ENV}: no mailboxes defined")

    # Entries keep their index into the JSON array, so the numbers in problem
    # reports still point at the right object after an earlier entry failed.
    entries: list[tuple[int, Mailbox]] = []
    problems: list[str] = []

    for index, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            problems.append(f"  entry #{index}: expected an object, got {type(entry).__name__}")
            continue
        try:
            entries.append((index, Mailbox.model_validate(entry)))
        except ValidationError as exc:
            problems.extend(_format_validation_error(index, exc))

    seen: dict[str, int] = {}
    for index, mailbox in entries:
        if mailbox.key in seen:
            problems.append(
                f"  entry #{index}: duplicate key {mailbox.key!r} (first seen at entry #{seen[mailbox.key]})"
            )
        else:
            seen[mailbox.key] = index

    # Two keys differing only in punctuation ('andrsk.cz' and 'andrsk-cz') derive
    # the same variable name. Caught here so it reads as a configuration error
    # rather than an opaque IMAP login failure for whichever mailbox lost.
    claimed: dict[str, str] = {}
    for index, mailbox in entries:
        owner = claimed.get(mailbox.password_env)
        if owner is not None:
            if owner != mailbox.key:
                problems.append(
                    f"  entry #{index}: mailbox {mailbox.key!r} shares {mailbox.password_env} "
                    f"with {owner!r}; keys must differ by more than punctuation"
                )
            continue
        claimed[mailbox.password_env] = mailbox.key
        if not env.get(mailbox.password_env, "").strip():
            problems.append(
                f"  mailbox {mailbox.key!r}: missing password, set {mailbox.password_env}"
            )

    if problems:
        raise MailboxConfigError(
            f"{CONFIG_ENV}: mailbox configuration is invalid:\n" + "\n".join(problems)
        )

    return [mailbox for _index, mailbox in entries]
