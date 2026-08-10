"""Parsing of IMAP THREAD responses into flat UID groups."""

from __future__ import annotations

from typing import Any


def _flatten(node: Any) -> list[int]:
    if isinstance(node, int):
        return [node]
    if isinstance(node, (list, tuple)):
        out: list[int] = []
        for item in node:
            out.extend(_flatten(item))
        return out
    return []


def parse_thread_response(data: Any) -> list[list[int]]:
    """Parse a raw THREAD response such as ``(1 2)(3)(4 (5)(6))`` into UID groups."""
    if not data:
        return []

    if isinstance(data, list):
        parts = []
        for item in data:
            if item is None:
                continue
            parts.append(item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item))
        text = " ".join(parts)
    elif isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    else:
        text = str(data)

    tokens: list[Any] = []
    digits = ""
    for char in text:
        if char.isdigit():
            digits += char
            continue
        if digits:
            tokens.append(int(digits))
            digits = ""
        if char in ("(", ")"):
            tokens.append(char)
    if digits:
        tokens.append(int(digits))

    stack: list[list] = []
    threads: list[list] = []
    for token in tokens:
        if token == "(":
            stack.append([])
        elif token == ")":
            if not stack:
                continue
            node = stack.pop()
            if stack:
                stack[-1].append(node)
            else:
                threads.append(node)
        elif stack:
            stack[-1].append(token)
        else:
            # A bare UID outside any parentheses is a thread of one.
            threads.append([token])

    return [_flatten(thread) for thread in threads if thread]


def fetch_thread_uids(conn) -> list[list[int]]:
    """Ask the server to thread the selected folder by REFERENCES.

    Returns an empty list when the server does not support THREAD; callers fall
    back to treating every message as its own thread.
    """
    try:
        threads = conn.thread("REFERENCES", "ALL")
    except Exception:
        threads = None

    if isinstance(threads, (bytes, str)):
        return parse_thread_response(threads)

    # imapclient runs the response through parse_response, which yields nested
    # tuples such as ((1, 2), (3,)) — not lists.
    if isinstance(threads, (list, tuple)):
        if threads and all(isinstance(item, int) for item in threads):
            # A flat sequence of UIDs: each stands alone, matching how
            # parse_thread_response treats UIDs outside any parentheses.
            return [[int(item)] for item in threads]
        out: list[list[int]] = []
        for thread in threads:
            if isinstance(thread, (int, list, tuple)):
                out.append(_flatten(thread))
            else:
                out.extend(parse_thread_response(thread))
        if out:
            return out

    # Fall back to the raw command for servers imapclient cannot parse.
    try:
        typ, data = conn._imap.uid("THREAD", "REFERENCES", "UTF-8", "ALL")
        if typ == "OK":
            return parse_thread_response(data)
    except Exception:
        pass

    return []


def group_for(threads: list[list[int]], uid: int) -> list[int]:
    """Return the thread containing ``uid``, or just ``uid`` if it stands alone."""
    for thread in threads:
        if uid in thread:
            return [item for item in thread if isinstance(item, int)]
    return [uid]
