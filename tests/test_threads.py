from emailmcp.imap.threads import fetch_thread_uids, group_for, parse_thread_response


class FakeIMAP:
    """Returns whatever the test wants from thread(); the raw fallback fails."""

    def __init__(self, result):
        self.result = result
        self.raw_calls = 0

        class _Raw:
            def uid(_self, *args):
                self.raw_calls += 1
                raise RuntimeError("THREAD unsupported")

        self._imap = _Raw()

    def thread(self, algorithm, criteria):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_a_thread_response_becomes_one_group_per_thread():
    # imapclient's parse_response yields nested tuples; the nesting is depth,
    # not separate threads, and a flat sequence must not collapse the inbox
    # into a single thread.
    assert fetch_thread_uids(FakeIMAP(((1, 2), (3,)))) == [[1, 2], [3]]
    assert fetch_thread_uids(FakeIMAP(((4, (5,), (6, 7)),))) == [[4, 5, 6, 7]]
    assert fetch_thread_uids(FakeIMAP((1, 2, 3))) == [[1], [2], [3]]
    assert parse_thread_response(b"(4 (5)(6 7))") == [[4, 5, 6, 7]]
    assert parse_thread_response([b"(1 2)", b"(3)"]) == [[1, 2], [3]]
    # Malformed input degrades rather than raising mid-listing.
    assert parse_thread_response(b")(1 2)") == [[1, 2]]
    assert parse_thread_response(None) == []


def test_a_server_without_THREAD_leaves_every_message_on_its_own():
    imap = FakeIMAP(RuntimeError("no THREAD capability"))
    assert fetch_thread_uids(imap) == []
    assert imap.raw_calls == 1
    assert group_for([[1, 2, 3]], 2) == [1, 2, 3]
    assert group_for([[1, 2]], 9) == [9]
