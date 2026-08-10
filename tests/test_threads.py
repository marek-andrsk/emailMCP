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


def test_imapclient_tuples_are_parsed_without_the_raw_fallback():
    # imapclient's parse_response yields nested tuples, e.g. ((1, 2), (3,)).
    imap = FakeIMAP(((1, 2), (3,), (4, 5, 6)))
    assert fetch_thread_uids(imap) == [[1, 2], [3], [4, 5, 6]]
    assert imap.raw_calls == 0


def test_nested_tuples_are_flattened():
    imap = FakeIMAP(((4, (5,), (6, 7)),))
    assert fetch_thread_uids(imap) == [[4, 5, 6, 7]]
    assert imap.raw_calls == 0


def test_lists_are_still_accepted():
    imap = FakeIMAP([[1, 2], [3]])
    assert fetch_thread_uids(imap) == [[1, 2], [3]]


def test_a_flat_sequence_becomes_singleton_threads():
    # Not merged into one thread, which would collapse the whole inbox.
    assert fetch_thread_uids(FakeIMAP((1, 2, 3))) == [[1], [2], [3]]


def test_an_unsupported_server_falls_back_and_then_gives_up():
    imap = FakeIMAP(RuntimeError("no THREAD capability"))
    assert fetch_thread_uids(imap) == []
    assert imap.raw_calls == 1


def test_flat_groups():
    assert parse_thread_response(b"(1 2 3)(4)") == [[1, 2, 3], [4]]


def test_nested_groups_are_flattened():
    assert parse_thread_response(b"(4 (5)(6 7))") == [[4, 5, 6, 7]]


def test_bare_uids_outside_parentheses_are_singleton_threads():
    assert parse_thread_response(b"1 2") == [[1], [2]]


def test_list_of_byte_chunks_is_joined():
    assert parse_thread_response([b"(1 2)", b"(3)"]) == [[1, 2], [3]]


def test_none_and_empty_are_handled():
    assert parse_thread_response(None) == []
    assert parse_thread_response(b"") == []
    assert parse_thread_response([None]) == []


def test_unbalanced_parentheses_do_not_raise():
    assert parse_thread_response(b")(1 2)") == [[1, 2]]


def test_group_for_finds_the_containing_thread():
    threads = [[1, 2, 3], [4, 5]]
    assert group_for(threads, 2) == [1, 2, 3]
    assert group_for(threads, 5) == [4, 5]


def test_group_for_unknown_uid_is_a_thread_of_one():
    assert group_for([[1, 2]], 9) == [9]
