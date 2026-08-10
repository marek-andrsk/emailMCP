"""Message references.

IMAP UIDs are only unique within one mailbox, so every message identifier the
server hands out carries its mailbox: ``andrsk.cz:4211``. Because the mailbox
travels with the UID, a caller cannot pair a UID from one mailbox with the key
of another — which would otherwise silently resolve to a different, existing
message and, in the worst case, produce a reply addressed to the wrong person.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

SEPARATOR = ":"


class InvalidMessageRef(ValueError):
    """Raised when a message id cannot be parsed or names an unknown mailbox."""


@dataclass(frozen=True, slots=True)
class MessageRef:
    mailbox: str
    uid: int

    def __str__(self) -> str:
        return f"{self.mailbox}{SEPARATOR}{self.uid}"


def format_ref(mailbox: str, uid: int) -> str:
    return str(MessageRef(mailbox, uid))


def _known(known_keys: Iterable[str]) -> str:
    keys = sorted(known_keys)
    return ", ".join(keys) if keys else "(none configured)"


def parse_ref(raw: object, known_keys: Iterable[str]) -> MessageRef:
    """Parse ``"<mailbox>:<uid>"``.

    Error messages always list the valid mailbox keys — they are the feedback
    loop a model uses to correct itself after a bad call.
    """
    known = list(known_keys)

    if isinstance(raw, MessageRef):
        text = str(raw)
    else:
        text = str(raw).strip()

    if not text:
        raise InvalidMessageRef(
            f"empty message id; expected '<mailbox>{SEPARATOR}<uid>'. Valid mailboxes: {_known(known)}"
        )

    if SEPARATOR not in text:
        raise InvalidMessageRef(
            f"message id {text!r} is missing its mailbox; expected '<mailbox>{SEPARATOR}<uid>', "
            f"for example '{(sorted(known) or ['mailbox'])[0]}{SEPARATOR}{text}'. "
            f"Valid mailboxes: {_known(known)}"
        )

    mailbox, _, uid_part = text.rpartition(SEPARATOR)
    mailbox = mailbox.strip()
    uid_part = uid_part.strip()

    if not uid_part.isdigit():
        raise InvalidMessageRef(
            f"message id {text!r} has a non-numeric UID {uid_part!r}; "
            f"expected '<mailbox>{SEPARATOR}<uid>'"
        )

    if mailbox not in known:
        raise InvalidMessageRef(
            f"unknown mailbox {mailbox!r} in message id {text!r}. Valid mailboxes: {_known(known)}"
        )

    return MessageRef(mailbox, int(uid_part))
