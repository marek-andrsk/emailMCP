"""Discovery of special-use folders (Sent, Drafts).

Migadu uses the plain names, but resolving through the IMAP special-use flags
keeps the server working against anything else without configuration.
"""

from __future__ import annotations

SENT_CANDIDATES = ("Sent Items", "Sent Mail", "Sent Messages", "INBOX.Sent", "[Gmail]/Sent Mail")
DRAFTS_CANDIDATES = ("Draft", "INBOX.Drafts", "[Gmail]/Drafts")


def _find(conn, exact: str, flag: str, candidates: tuple[str, ...]) -> str | None:
    try:
        folders = conn.list_folders()
    except Exception:
        return None

    for _flags, _delim, name in folders:
        if name.lower() == exact.lower():
            return name

    for flags, _delim, name in folders:
        decoded = {f.decode() if isinstance(f, bytes) else f for f in flags}
        if flag in decoded:
            return name

    by_lower = {name.lower(): name for _f, _d, name in folders}
    for candidate in candidates:
        hit = by_lower.get(candidate.lower())
        if hit:
            return hit

    return None


def find_sent(conn) -> str | None:
    return _find(conn, "Sent", "\\Sent", SENT_CANDIDATES)


def find_drafts(conn) -> str | None:
    return _find(conn, "Drafts", "\\Drafts", DRAFTS_CANDIDATES)
