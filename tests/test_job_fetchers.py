"""Fetcher tests, against saved sample responses (tests/fixtures/fetchers/)."""
import json

import pytest

from job.fetchers import amazon, ashby, greenhouse, lever, smartrecruiters, workday
from job.fetchers.base import FetchError

from conftest import FIXTURES_DIR


def load_fixture(name: str):
    return json.loads((FIXTURES_DIR / "fetchers" / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse):
        self._response = response
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params))
        return self._response

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, json))
        return self._response


def test_greenhouse_fetch():
    session = FakeSession(FakeResponse(payload=load_fixture("greenhouse.json")))
    company = {"id": 1, "name": "Acme", "feed_type": "greenhouse", "feed_id": "acme"}
    postings = greenhouse.fetch(company, session=session)
    assert len(postings) == 2
    assert postings[0].title == "MBA Product Manager Intern, Summer 2027"
    assert postings[0].location_text == "San Francisco, CA"
    assert postings[0].url == "https://boards.greenhouse.io/acme/jobs/1001"
    assert postings[0].posted_date == "2026-09-01"


def test_greenhouse_requires_feed_id():
    with pytest.raises(FetchError):
        greenhouse.fetch({"id": 1, "name": "Acme", "feed_type": "greenhouse", "feed_id": None})


def test_lever_fetch():
    session = FakeSession(FakeResponse(payload=load_fixture("lever.json")))
    company = {"id": 1, "name": "Acme", "feed_type": "lever", "feed_id": "acme"}
    postings = lever.fetch(company, session=session)
    assert len(postings) == 1
    assert postings[0].title == "MBA Product Manager Intern - Summer 2027"
    assert postings[0].location_text == "New York, NY"
    assert postings[0].posted_date == "2026-01-01"


def test_ashby_fetch():
    session = FakeSession(FakeResponse(payload=load_fixture("ashby.json")))
    company = {"id": 1, "name": "Acme", "feed_type": "ashby", "feed_id": "acme"}
    postings = ashby.fetch(company, session=session)
    assert len(postings) == 1
    assert postings[0].location_text == "Tokyo, Japan"
    assert postings[0].posted_date == "2026-09-01"


def test_smartrecruiters_fetch():
    session = FakeSession(FakeResponse(payload=load_fixture("smartrecruiters.json")))
    company = {"id": 1, "name": "Acme", "feed_type": "smartrecruiters", "feed_id": "acme"}
    postings = smartrecruiters.fetch(company, session=session)
    assert len(postings) == 1
    assert postings[0].location_text == "Seattle, WA, us"
    assert postings[0].url == "https://jobs.smartrecruiters.com/acme/posting-1"


def test_workday_fetch():
    session = FakeSession(FakeResponse(payload=load_fixture("workday.json")))
    company = {
        "id": 1, "name": "Acme", "feed_type": "workday", "feed_id": "acme",
        "careers_url": "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers",
    }
    postings = workday.fetch(company, session=session)
    assert len(postings) == 1
    assert postings[0].url == "https://acme.wd5.myworkdayjobs.com/en-US/job/Santa-Clara--CA/MBA-Product-Manager-Intern_JR1234567"
    assert postings[0].posted_date == "2026-09-01"


def test_workday_requires_full_careers_url():
    with pytest.raises(FetchError):
        workday.fetch({"id": 1, "name": "Acme", "feed_type": "workday", "feed_id": "acme", "careers_url": "https://acme.wd5.myworkdayjobs.com"})


def test_amazon_fetch():
    session = FakeSession(FakeResponse(payload=load_fixture("amazon.json")))
    postings = amazon.fetch({"id": 1, "name": "Amazon", "feed_type": "amazon_jobs"}, session=session)
    assert len(postings) == 1
    assert postings[0].url == "https://www.amazon.jobs/en/jobs/2712345/mba-product-manager-intern"
    assert postings[0].posted_date == "2026-09-01"


def test_fetcher_raises_on_bad_shape():
    session = FakeSession(FakeResponse(payload={"unexpected": True}))
    with pytest.raises(FetchError):
        greenhouse.fetch({"id": 1, "name": "Acme", "feed_type": "greenhouse", "feed_id": "acme"}, session=session)


def test_fetcher_raises_on_http_error():
    session = FakeSession(FakeResponse(status_code=500))
    with pytest.raises(FetchError):
        greenhouse.fetch({"id": 1, "name": "Acme", "feed_type": "greenhouse", "feed_id": "acme"}, session=session)


def test_fetcher_raises_on_blocked_request():
    session = FakeSession(FakeResponse(status_code=403))
    with pytest.raises(FetchError):
        greenhouse.fetch({"id": 1, "name": "Acme", "feed_type": "greenhouse", "feed_id": "acme"}, session=session)
