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
    # The link to a Workday posting includes the site name. The old expected
    # value here left it out ("/en-US/job/..."), which is not a page Workday
    # serves; every real posting link on file has the site in it (BR-001.1).
    assert postings[0].url == (
        "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/job/Santa-Clara--CA/MBA-Product-Manager-Intern_JR1234567"
    )
    assert postings[0].posted_date == "2026-09-01"


def test_workday_requires_full_careers_url():
    with pytest.raises(FetchError):
        workday.fetch({"id": 1, "name": "Acme", "feed_type": "workday", "feed_id": "acme", "careers_url": "https://acme.wd5.myworkdayjobs.com"})


# -- BR-001.1: Workday site links are read whatever their shape ---------------

@pytest.mark.parametrize("link, host, locale, site", [
    ("https://zebra.wd1.myworkdayjobs.com/Zebra_Careers", "zebra.wd1.myworkdayjobs.com", None, "Zebra_Careers"),
    ("https://autodesk.wd1.myworkdayjobs.com/uni", "autodesk.wd1.myworkdayjobs.com", None, "uni"),
    ("https://kla.wd1.myworkdayjobs.com/UR", "kla.wd1.myworkdayjobs.com", None, "UR"),
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", "nvidia.wd5.myworkdayjobs.com", None,
     "NVIDIAExternalCareerSite"),
    ("https://ontoinnovation.wd1.myworkdayjobs.com/en-US/Careers", "ontoinnovation.wd1.myworkdayjobs.com", "en-US",
     "Careers"),
    ("https://fiserv.wd5.myworkdayjobs.com/EXT?q=intern", "fiserv.wd5.myworkdayjobs.com", None, "EXT"),
    ("https://tel.wd3.myworkdayjobs.com/en-US/TEL-Careers/", "tel.wd3.myworkdayjobs.com", "en-US", "TEL-Careers"),
    ("https://hp.wd5.myworkdayjobs.com/en-US/ExternalCareerSite/job/Product-Management-Intern_UNI4358-1",
     "hp.wd5.myworkdayjobs.com", "en-US", "ExternalCareerSite"),
])
def test_workday_site_link_is_read_with_or_without_a_locale(link, host, locale, site):
    assert workday.parse_site_link(link) == (host, locale, site)


@pytest.mark.parametrize("link", [
    "", "https://broadcom.wd1.myworkdayjobs.com", "https://broadcom.wd1.myworkdayjobs.com/",
    "https://acme.wd5.myworkdayjobs.com/en-US", "https://www.micron.com/careers/university-recruiting",
    "https://app.joinhandshake.com/employers/nissan-north-america-107938",
])
def test_workday_site_link_rejects_links_that_name_no_site(link):
    assert workday.parse_site_link(link) is None


def test_workday_fetch_works_for_a_link_with_no_locale():
    session = FakeSession(FakeResponse(payload=load_fixture("workday.json")))
    company = {
        "id": 1, "name": "Zebra Technologies", "feed_type": "workday", "feed_id": "zebra",
        "careers_url": "https://zebra.wd1.myworkdayjobs.com/Zebra_Careers",
    }
    postings = workday.fetch(company, session=session)
    assert len(postings) == 1
    assert session.calls[0][1] == "https://zebra.wd1.myworkdayjobs.com/wday/cxs/zebra/Zebra_Careers/jobs"
    assert postings[0].url == (
        "https://zebra.wd1.myworkdayjobs.com/Zebra_Careers/job/Santa-Clara--CA/MBA-Product-Manager-Intern_JR1234567"
    )


def test_workday_fetch_prefers_the_feed_address_over_the_careers_page():
    # BR-001.3: the careers page can change to a marketing page without
    # breaking the feed, because the feed has its own address.
    session = FakeSession(FakeResponse(payload=load_fixture("workday.json")))
    company = {
        "id": 1, "name": "Micron", "feed_type": "workday", "feed_id": "micron",
        "feed_url": "https://micron.wd1.myworkdayjobs.com/External",
        "careers_url": "https://www.micron.com/careers/university-recruiting",
    }
    postings = workday.fetch(company, session=session)
    assert len(postings) == 1
    assert session.calls[0][1] == "https://micron.wd1.myworkdayjobs.com/wday/cxs/micron/External/jobs"


def test_workday_fetch_takes_the_tenant_from_the_host_not_a_stale_feed_id():
    session = FakeSession(FakeResponse(payload=load_fixture("workday.json")))
    company = {
        "id": 1, "name": "Wolters Kluwer", "feed_type": "workday", "feed_id": "wolterskluwer",
        "feed_url": "https://wk.wd3.myworkdayjobs.com/External",
    }
    workday.fetch(company, session=session)
    assert session.calls[0][1] == "https://wk.wd3.myworkdayjobs.com/wday/cxs/wk/External/jobs"


class PagedWorkdaySession:
    """Answers the Workday listing the way the real one does: 20 a page, the
    total on the first page only, filtered by the search text."""

    def __init__(self, jobs_by_search: dict):
        self.jobs_by_search = jobs_by_search
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        jobs = self.jobs_by_search.get(json["searchText"], [])
        page = jobs[json["offset"]: json["offset"] + json["limit"]]
        return FakeResponse(payload={"total": len(jobs) if json["offset"] == 0 else 0, "jobPostings": page})


def _workday_job(n, prefix="intern"):
    return {"title": f"Intern {n}", "locationsText": "Austin, TX", "postedOn": "Posted Today",
            "externalPath": f"/job/Austin/{prefix}-{n}", "bulletFields": [f"JR{prefix}{n}"]}


def test_workday_fetch_asks_for_internship_and_mba_postings_and_reads_every_page():
    session = PagedWorkdaySession({
        "intern": [_workday_job(n) for n in range(45)],
        "MBA": [_workday_job(n, prefix="mba") for n in range(3)],
    })
    company = {"id": 1, "name": "Acme", "feed_type": "workday", "feed_url": "https://acme.wd5.myworkdayjobs.com/Acme"}
    postings = workday.fetch(company, session=session)
    assert len(postings) == 48
    assert {c["searchText"] for c in session.calls} == set(workday.SEARCH_TERMS)
    assert [c["offset"] for c in session.calls if c["searchText"] == "intern"] == [0, 20, 40]


def test_workday_fetch_lists_a_posting_once_when_both_searches_find_it():
    shared = _workday_job(1)
    session = PagedWorkdaySession({"intern": [shared], "MBA": [shared]})
    company = {"id": 1, "name": "Acme", "feed_type": "workday", "feed_url": "https://acme.wd5.myworkdayjobs.com/Acme"}
    assert len(workday.fetch(company, session=session)) == 1


def test_workday_fetch_stops_at_the_page_cap_so_a_huge_site_cannot_run_away():
    session = PagedWorkdaySession({"intern": [_workday_job(n) for n in range(1000)], "MBA": []})
    company = {"id": 1, "name": "Acme", "feed_type": "workday", "feed_url": "https://acme.wd5.myworkdayjobs.com/Acme"}
    postings = workday.fetch(company, session=session)
    assert len(postings) == workday.MAX_PAGES_PER_SEARCH * workday.PAGE_SIZE
    assert len([c for c in session.calls if c["searchText"] == "intern"]) == workday.MAX_PAGES_PER_SEARCH


def test_workday_is_marked_as_a_partial_listing_and_the_full_feeds_are_not():
    from job.fetchers import listing_is_complete
    assert listing_is_complete("workday") is False
    for feed_type in ("greenhouse", "lever", "ashby", "smartrecruiters", "amazon_jobs"):
        assert listing_is_complete(feed_type) is True


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


# -- BR-004: a refusal carries its details --------------------------------------

class HeaderResponse(FakeResponse):
    def __init__(self, status_code, headers):
        super().__init__(status_code=status_code)
        self.headers = headers


def test_a_failed_request_carries_its_status_and_the_wait_the_site_asked_for():
    from job.fetchers.base import FetchError, get_json, post_json

    session = FakeSession(HeaderResponse(429, {"Retry-After": "12"}))
    with pytest.raises(FetchError) as posted:
        post_json("https://x.example/jobs", json_body={}, session=session)
    with pytest.raises(FetchError) as got:
        get_json("https://x.example/jobs", session=session)

    assert (posted.value.status, posted.value.retry_after) == (429, 12.0)
    assert (got.value.status, got.value.retry_after) == (429, 12.0)
    assert "returned HTTP 429" in str(posted.value)


def test_a_failed_request_with_no_headers_still_reports_its_status():
    from job.fetchers.base import FetchError, post_json

    with pytest.raises(FetchError) as caught:
        post_json("https://x.example/jobs", json_body={}, session=FakeSession(FakeResponse(status_code=503)))

    assert (caught.value.status, caught.value.retry_after) == (503, None)


@pytest.mark.parametrize("error, rate_limited, temporary", [
    ("https://x.example/jobs returned HTTP 429", True, True),
    ('https://x.example/jobs returned HTTP 429 (search "intern", page 1, after 0 answered requests)', True, True),
    ("https://x.example/jobs returned HTTP 503", False, True),
    ("https://x.example/jobs returned HTTP 500", False, True),
    ("request to https://x.example/jobs failed: Read timed out.", False, True),
    ("https://x.example/jobs returned HTTP 404", False, False),
    ("https://x.example/jobs returned HTTP 400", False, False),
    ("https://x.example/jobs blocked the request (403); not retrying or working around it", False, False),
    ("https://x.example/jobs did not return JSON", False, False),
    ("Acme: feed address isn't a Workday site link", False, False),
    ("", False, False),
    (None, False, False),
])
def test_errors_are_sorted_into_refusals_temporary_trouble_and_real_answers(error, rate_limited, temporary):
    from job.fetchers.base import is_rate_limit_error, is_temporary_error

    assert is_rate_limit_error(error) is rate_limited
    assert is_temporary_error(error) is temporary
