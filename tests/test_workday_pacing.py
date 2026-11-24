"""BR-004: the Workday reader slows down when asked, waits, tries once more,
keeps what it has read, and stops asking a site that keeps refusing.

Time is faked: `_clock` is a number these tests move, and `_sleep` moves it
and records how long the reader chose to wait.
"""
import pytest

from job.fetchers import workday
from job.fetchers.base import FetchError

NVIDIA = {"id": 1, "name": "NVIDIA", "feed_type": "workday", "feed_id": "nvidia",
          "feed_url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"}
ZEBRA = {"id": 2, "name": "Zebra", "feed_type": "workday", "feed_id": "zebra",
         "feed_url": "https://zebra.wd1.myworkdayjobs.com/Zebra_Careers"}
HP = {"id": 3, "name": "HP Inc.", "feed_type": "workday", "feed_id": "hp",
      "feed_url": "https://hp.wd5.myworkdayjobs.com/ExternalCareerSite"}


class Resp:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.headers = headers or {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def page(count, total=None, prefix="intern"):
    jobs = [{"title": f"Intern {n}", "locationsText": "Austin, TX", "postedOn": "Posted Today",
             "externalPath": f"/job/Austin/{prefix}-{n}"} for n in range(count)]
    return Resp(payload={"total": count if total is None else total, "jobPostings": jobs})


def refused(retry_after=None):
    return Resp(429, headers={} if retry_after is None else {"Retry-After": str(retry_after)})


class Time:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Scripted:
    """Answers requests from a list, in order, and notes when each was sent."""

    def __init__(self, time, responses):
        self.time = time
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"at": self.time.now, "url": url, "search": json["searchText"], "offset": json["offset"]})
        return self.responses.pop(0)


@pytest.fixture
def time(monkeypatch):
    fake = Time()
    workday.reset_pacing()
    monkeypatch.setattr(workday, "_clock", fake.clock)
    monkeypatch.setattr(workday, "_sleep", fake.sleep)
    yield fake
    workday.reset_pacing()


# -- BR-004.1: a steady pace ----------------------------------------------------

def test_two_requests_are_never_sent_closer_together_than_the_pause(time):
    session = Scripted(time, [page(3), page(2, prefix="mba")])

    workday.fetch(NVIDIA, session=session)

    assert session.calls[1]["at"] - session.calls[0]["at"] >= workday.REQUEST_PAUSE_SECONDS
    assert time.sleeps == [workday.REQUEST_PAUSE_SECONDS]  # nothing to wait for before the first


def test_the_pause_holds_from_one_company_to_the_next(time):
    # The companies share Workday's hosts, so the pace is for Workday as a whole.
    first = Scripted(time, [page(1), page(0)])
    second = Scripted(time, [page(1), page(0)])

    workday.fetch(NVIDIA, session=first)
    workday.fetch(ZEBRA, session=second)

    assert second.calls[0]["at"] - first.calls[-1]["at"] >= workday.REQUEST_PAUSE_SECONDS


def test_no_wait_is_added_when_enough_time_has_already_passed(time):
    workday.fetch(NVIDIA, session=Scripted(time, [page(1), page(0)]))
    time.now += 30  # the run was busy with other feeds
    asked_at = time.now

    session = Scripted(time, [page(1), page(0)])
    workday.fetch(ZEBRA, session=session)

    assert session.calls[0]["at"] == asked_at


# -- BR-004.1 and BR-004.3: wait as long as asked, then once more ----------------

def test_a_refusal_is_waited_out_for_the_time_the_site_names_and_tried_once_more(time):
    session = Scripted(time, [refused(retry_after=7), page(3), page(1, prefix="mba")])

    postings = workday.fetch(NVIDIA, session=session)

    assert len(postings) == 4
    assert 7.0 in time.sleeps
    assert [(c["search"], c["offset"]) for c in session.calls] == [("intern", 0), ("intern", 0), ("MBA", 0)]
    assert session.calls[1]["at"] - session.calls[0]["at"] >= 7


def test_a_refusal_that_names_no_wait_gets_the_default_one(time):
    workday.fetch(NVIDIA, session=Scripted(time, [refused(), page(1), page(0)]))
    assert workday.DEFAULT_RETRY_WAIT_SECONDS in time.sleeps


@pytest.mark.parametrize("header", ["Wed, 25 Nov 2026 14:00:00 GMT", "soon", "-5", ""])
def test_a_wait_we_cannot_read_gets_the_default_one(time, header):
    workday.fetch(NVIDIA, session=Scripted(time, [refused(retry_after=header), page(1), page(0)]))
    assert workday.DEFAULT_RETRY_WAIT_SECONDS in time.sleeps


def test_a_very_long_wait_is_cut_to_the_cap(time):
    workday.fetch(NVIDIA, session=Scripted(time, [refused(retry_after=600), page(1), page(0)]))
    assert max(time.sleeps) == workday.MAX_RETRY_WAIT_SECONDS


def test_the_same_request_is_never_sent_more_than_twice(time):
    session = Scripted(time, [refused(), refused(), page(1)])

    with pytest.raises(FetchError) as caught:
        workday.fetch(NVIDIA, session=session)

    assert len(session.calls) == 2
    assert caught.value.status == 429


def test_all_the_waiting_in_a_run_shares_one_allowance(time, monkeypatch):
    monkeypatch.setattr(workday, "STOP_AFTER_REFUSED_READS", 1000)  # not the rule under test
    tries_that_fit = int(workday.RETRY_WAIT_SECONDS_PER_RUN // workday.DEFAULT_RETRY_WAIT_SECONDS)
    for _ in range(tries_that_fit):
        with pytest.raises(FetchError):
            workday.fetch(NVIDIA, session=Scripted(time, [refused(), refused()]))
    retry_waits = [s for s in time.sleeps if s == workday.DEFAULT_RETRY_WAIT_SECONDS]
    assert sum(retry_waits) == workday.RETRY_WAIT_SECONDS_PER_RUN

    # The allowance is spent: the next refusal fails at once, with no wait and no second request.
    session = Scripted(time, [refused(), page(1)])
    with pytest.raises(FetchError):
        workday.fetch(ZEBRA, session=session)

    assert len(session.calls) == 1
    assert [s for s in time.sleeps if s == workday.DEFAULT_RETRY_WAIT_SECONDS] == retry_waits


def test_an_error_that_is_not_a_refusal_is_not_waited_on_or_retried(time):
    session = Scripted(time, [Resp(500), page(1)])

    with pytest.raises(FetchError) as caught:
        workday.fetch(NVIDIA, session=session)

    assert len(session.calls) == 1
    assert caught.value.status == 500
    assert time.sleeps == []


# -- BR-004.1: keep what was read ----------------------------------------------

def test_a_company_refused_on_a_later_page_keeps_the_pages_already_read(time):
    session = Scripted(time, [page(20, total=45), refused(), refused(), page(5, prefix="mba")])

    postings = workday.fetch(NVIDIA, session=session)

    assert len(postings) == 20
    assert len(session.calls) == 3  # the site said stop, so the MBA search is not asked


def test_a_company_refused_on_its_second_search_keeps_the_first(time):
    postings = workday.fetch(NVIDIA, session=Scripted(time, [page(5), refused(), refused()]))
    assert len(postings) == 5


def test_a_company_answered_with_nothing_and_then_refused_has_still_been_read(time):
    # The address works and the site answered: this is a read, not a failure.
    assert workday.fetch(NVIDIA, session=Scripted(time, [page(0), refused(), refused()])) == []


# -- BR-004.4: say which request was refused ------------------------------------

def test_a_refused_read_says_which_request_it_was(time):
    with pytest.raises(FetchError) as caught:
        workday.fetch(NVIDIA, session=Scripted(time, [refused(), refused()]))

    message = str(caught.value)
    assert "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs returned HTTP 429" in message
    assert 'search "intern", page 1, after 0 answered requests' in message


# -- BR-004.3: stop asking a site that keeps refusing ----------------------------

def _refuse(time, company):
    with pytest.raises(FetchError):
        workday.fetch(company, session=Scripted(time, [refused(), refused()]))


def test_after_three_refused_companies_in_a_row_the_rest_on_that_data_center_are_not_asked(time):
    for _ in range(workday.STOP_AFTER_REFUSED_READS):
        _refuse(time, NVIDIA)  # wd5
    session = Scripted(time, [page(1), page(0)])

    with pytest.raises(FetchError) as caught:
        workday.fetch(HP, session=session)  # also wd5

    assert session.calls == []
    assert caught.value.status == 429
    assert "HTTP 429" in str(caught.value)
    assert "HP Inc." in str(caught.value) and "not asked" in str(caught.value) and "wd5" in str(caught.value)


def test_a_data_center_that_has_not_refused_us_is_still_asked(time):
    # Run 111: three companies on wd1 were refused, then Microchip and Fiserv
    # on wd5 read. One data center's refusals say nothing about another's.
    for _ in range(workday.STOP_AFTER_REFUSED_READS):
        _refuse(time, NVIDIA)  # wd5

    session = Scripted(time, [page(2), page(0)])
    postings = workday.fetch(ZEBRA, session=session)  # wd1

    assert len(postings) == 2


def test_a_company_that_reads_starts_the_count_again(time):
    _refuse(time, NVIDIA)
    _refuse(time, NVIDIA)
    workday.fetch(HP, session=Scripted(time, [page(1), page(0)]))  # same data center, and it reads
    _refuse(time, NVIDIA)
    _refuse(time, NVIDIA)

    session = Scripted(time, [page(1), page(0)])
    workday.fetch(HP, session=session)

    assert len(session.calls) == 2


def test_a_new_run_starts_clean(time):
    for _ in range(workday.STOP_AFTER_REFUSED_READS):
        _refuse(time, NVIDIA)

    workday.reset_pacing()

    session = Scripted(time, [page(1), page(0)])
    workday.fetch(HP, session=session)
    assert len(session.calls) == 2
