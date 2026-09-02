"""The set of configured mailboxes, and lookup of stores by key or message id."""

from __future__ import annotations

from typing import Mapping

from .config import Mailbox, password_for
from .imap.store import MailStore
from .refs import MessageRef, parse_ref


class UnknownMailbox(ValueError):
    pass


class Registry:
    """Maps mailbox keys to stores.

    Stores are built eagerly so a bad password surfaces at startup, but the
    connections underneath stay lazy — an idle mailbox costs nothing.
    """

    def __init__(self, mailboxes: list[Mailbox], env: Mapping[str, str] | None = None) -> None:
        self._stores: dict[str, MailStore] = {
            mailbox.key: MailStore(mailbox, password_for(mailbox, env)) for mailbox in mailboxes
        }

    @property
    def keys(self) -> list[str]:
        return list(self._stores)

    def get(self, key: object) -> MailStore:
        name = str(key).strip() if key is not None else ""
        store = self._stores.get(name)
        if store is None:
            raise UnknownMailbox(
                f"unknown mailbox {name!r}. Valid mailboxes: {', '.join(self.keys)}"
            )
        return store

    def resolve(self, message_id: object) -> tuple[MailStore, int]:
        ref: MessageRef = parse_ref(message_id, self.keys)
        return self._stores[ref.mailbox], ref.uid

    def describe(self) -> list[dict]:
        return [
            {
                "mailbox": store.key,
                "address": store.address,
                "label": store.mailbox.label or "",
                "message_id_prefix": f"{store.key}:",
            }
            for store in self._stores.values()
        ]

    def close(self) -> None:
        for store in self._stores.values():
            store.close()
