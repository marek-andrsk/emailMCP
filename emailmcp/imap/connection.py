"""Connection lifecycle for a single mailbox.

One lazily-created IMAP connection per mailbox, guarded by a reentrant lock.
The lock matters because ``imapclient`` connections are not thread-safe while
FastMCP runs synchronous tools in worker threads — two concurrent tool calls on
the same mailbox would otherwise interleave on one socket.

``folder()`` makes the selected folder explicit and restores the previous
selection on exit, so nested operations (a draft that reads its own original)
cannot leave the connection pointing somewhere unexpected.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

import imapclient


class Connection:
    def __init__(self, host: str, port: int, user: str, password: str) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._lock = threading.RLock()
        self._conn: imapclient.IMAPClient | None = None
        self._selected: tuple[str, bool] | None = None
        self._depth = 0

    # --- lifecycle ---------------------------------------------------------

    def _connect(self) -> imapclient.IMAPClient:
        conn = imapclient.IMAPClient(self._host, port=self._port, ssl=True)
        conn.login(self._user, self._password)
        self._selected = None
        return conn

    def _live(self) -> imapclient.IMAPClient:
        if self._conn is None:
            self._conn = self._connect()
            return self._conn
        try:
            self._conn.noop()
        except Exception:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.logout()
                except Exception:
                    pass
            self._conn = None
            self._selected = None

    # --- access ------------------------------------------------------------

    @contextmanager
    def session(self) -> Iterator[imapclient.IMAPClient]:
        """Hold the connection without touching the folder selection.

        The liveness probe only runs for the outermost acquisition, so nested
        use costs no extra round trips.
        """
        with self._lock:
            if self._depth == 0:
                conn = self._live()
            else:
                assert self._conn is not None  # depth > 0 implies a live connection
                conn = self._conn
            self._depth += 1
            try:
                yield conn
            finally:
                self._depth -= 1

    def _select(self, conn: imapclient.IMAPClient, name: str, readonly: bool) -> None:
        if self._selected == (name, readonly):
            return
        # A failed SELECT deselects the mailbox server-side, so the cache is
        # dropped before the attempt rather than set after it. Recording the
        # folder only on success would leave the cache naming a folder that is
        # no longer selected, and the next call would skip SELECT and fail.
        self._selected = None
        conn.select_folder(name, readonly=readonly)
        self._selected = (name, readonly)

    @contextmanager
    def folder(self, name: str, readonly: bool = True) -> Iterator[imapclient.IMAPClient]:
        with self.session() as conn:
            previous = self._selected
            # Only an inner block has an enclosing selection worth restoring;
            # at the top level, leaving the folder selected costs nothing.
            nested = self._depth > 1
            try:
                # Inside the try so that a failed SELECT still restores the
                # enclosing folder — callers that swallow the error (a missing
                # Sent folder, say) keep working in the outer block.
                self._select(conn, name, readonly)
                yield conn
            finally:
                if nested and previous is not None and previous != self._selected:
                    try:
                        self._select(conn, previous[0], previous[1])
                    except Exception:
                        # Leave the cache empty so the next select is forced.
                        self._selected = None
