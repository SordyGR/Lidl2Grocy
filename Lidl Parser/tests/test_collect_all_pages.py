"""collect_tids walks ?page=N until an empty page (RED first)."""


class _El:
    def __init__(self, href):
        self._href = href

    def get_attribute(self, _):
        return self._href


class _Pages:
    def __init__(self, pages, flaky=()):
        self._pages = pages  # page_num -> list of tids
        self._flaky = set(flaky)  # pages whose FIRST wait raises (slow JS), then load
        self._attempts = {}
        self.url = ""
        self.visited = []
        self._cur = 1  # caller gotos page 1 before collect_tids; start there

    def goto(self, url):
        import re
        self.url = url
        m = re.search(r"page=(\d+)", url)
        self._cur = int(m.group(1)) if m else 1
        self.visited.append(self._cur)

    def wait_for_selector(self, sel, timeout=15000):
        n = self._attempts.get(self._cur, 0)
        self._attempts[self._cur] = n + 1
        if self._cur in self._flaky and n == 0:
            raise Exception("slow js load")
        if not self._pages.get(self._cur):
            raise Exception("no links")

    def query_selector_all(self, sel):
        return [_El(f"https://x/mre/purchase-detail?t={t}")
                for t in self._pages.get(self._cur, [])]

    def query_selector(self, sel):
        return None  # no next-link on any page


def test_walks_until_empty_page():
    import fetch_lidl as fl
    pg = _Pages({1: ["a", "b"], 2: ["c"], 3: []})
    assert fl.collect_tids(pg) == ["a", "b", "c"]
    assert pg.visited == [2, 3, 3]  # page 1 by caller; empty page retried once


def test_max_pages_cap():
    import fetch_lidl as fl
    pg = _Pages({1: ["a"], 2: ["b"], 3: ["c"]})
    assert fl.collect_tids(pg, max_pages=1) == ["a"]


def test_with_pages_returns_tid_page_pairs():
    import fetch_lidl as fl
    pg = _Pages({1: ["a"], 2: ["b"], 3: []})
    assert fl.collect_tids(pg, with_pages=True) == [("a", 1), ("b", 2)]


def test_slow_page_retried_not_truncated():
    import fetch_lidl as fl
    pg = _Pages({1: ["a"], 2: ["b"], 3: []}, flaky=(2,))
    assert fl.collect_tids(pg) == ["a", "b"]


def test_repeated_tids_do_not_end_walk():
    import fetch_lidl as fl
    pg = _Pages({1: ["a"], 2: ["a"], 3: ["b"], 4: []})
    assert fl.collect_tids(pg) == ["a", "b"]


def test_stuck_pagination_terminates():
    import fetch_lidl as fl

    class Stuck(_Pages):
        def query_selector_all(self, sel):
            return [_El("https://x/mre/purchase-detail?t=a")]

    pg = Stuck({1: ["a"]})
    assert fl.collect_tids(pg) == ["a"]
    assert len(pg.visited) <= 1 + fl.STALE_PAGE_LIMIT + 1  # bounded walk
