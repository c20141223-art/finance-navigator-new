from stock_screener.config import HttpConfig
from stock_screener.http_client import RateLimitedClient


class _FakeResponse:
    def __init__(self, status_code, text="{}"):
        self.status_code = status_code
        self.text = text
        self.content = text.encode()


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        self.last_headers = headers
        return self._responses.pop(0)


def _config(**overrides):
    base = dict(timeout_seconds=1, min_interval_seconds=0, max_retries=3, retry_backoff_seconds=0)
    base.update(overrides)
    return HttpConfig(**base)


def test_get_succeeds_first_try():
    session = _FakeSession([_FakeResponse(200, "ok")])
    client = RateLimitedClient(_config(), session=session)
    outcome = client.get("https://example.com/a")
    assert outcome.ok
    assert outcome.attempts == 1
    assert session.calls == 1


def test_get_passes_per_request_headers():
    session = _FakeSession([_FakeResponse(200, "ok")])
    client = RateLimitedClient(_config(), session=session)
    client.get("https://example.com/a", headers={"Referer": "https://www.twse.com.tw/"})
    assert session.last_headers == {"Referer": "https://www.twse.com.tw/"}


def test_get_retries_then_succeeds():
    session = _FakeSession([_FakeResponse(500), _FakeResponse(200, "ok")])
    client = RateLimitedClient(_config(max_retries=3), session=session)
    outcome = client.get("https://example.com/a")
    assert outcome.ok
    assert outcome.attempts == 2
    assert session.calls == 2


def test_get_exhausts_retries_and_reports_failure():
    session = _FakeSession([_FakeResponse(500), _FakeResponse(500), _FakeResponse(500)])
    client = RateLimitedClient(_config(max_retries=3), session=session)
    outcome = client.get("https://example.com/a")
    assert not outcome.ok
    assert outcome.error == "HTTP 500"
    assert session.calls == 3


def test_min_interval_throttles_same_host(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("time.monotonic", _make_fake_clock([0.0, 0.0, 0.1, 0.1]))

    session = _FakeSession([_FakeResponse(200, "a"), _FakeResponse(200, "b")])
    client = RateLimitedClient(_config(min_interval_seconds=5.0), session=session)
    client.get("https://example.com/a")
    client.get("https://example.com/a")

    assert any(s > 0 for s in sleeps)


def _make_fake_clock(values):
    it = iter(values)

    def _clock():
        try:
            return next(it)
        except StopIteration:
            return values[-1]

    return _clock
