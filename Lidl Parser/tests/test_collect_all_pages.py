"""collect_tids walks ?page=N until an empty page (RED first)."""


class _El:
    def __init__(self, href):
        self._href = href

    def get_attribute(self, _):
        return self._href


class _Pages:
    def __init__(self, pages):
        self._pages = pages  # page_num -> list of tids
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
    assert pg.visited == [2, 3]  # page 1 is goto'd by the caller, not collect_tids


def test_max_pages_cap():
    import fetch_lidl as fl
    pg = _Pages({1: ["a"], 2: ["b"], 3: ["c"]})
    assert fl.collect_tids(pg, max_pages=1) == ["a"]


def test_with_pages_returns_tid_page_pairs():
    import fetch_lidl as fl
    pg = _Pages({1: ["a"], 2: ["b"], 3: []})
    assert fl.collect_tids(pg, with_pages=True) == [("a", 1), ("b", 2)]
