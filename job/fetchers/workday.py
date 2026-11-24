"""Workday's unofficial CXS (career site) JSON API.

Not documented or officially supported by Workday, but widely relied on --
it's the same endpoint the public careers page itself calls. The request is
built from a site link, `https://<tenant>.wdN.myworkdayjobs.com/<site>`, with
or without a locale before the site name. That link is the company's
`feed_url`; `careers_url` is the fallback for rows from before `feed_url`
existed (BR-001.3: the careers page can change without breaking the feed).

The listing is partial on purpose. A large company lists thousands of jobs
20 at a time, so this asks only for internship and MBA postings and stops
after a few pages of each (`job/fetchers/__init__.py`'s
`PARTIAL_LISTING_FEEDS` tells the rest of the job not to read a posting's
absence here as "closed").

Workday answers "too many requests" (HTTP 429) to a reader that asks quickly,
and the companies share its hosts, so the pace is kept for Workday as a whole
(BR-004): a pause between any two requests, one wait and one more try when
asked to slow down, and no more requests in a run once it keeps refusing.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from job.fetchers.base import FetchError, post_json
from job.models import FetchedPosting

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# en-US, ja-JP, zh-CN, es-419. A site name is never shaped like this.
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Za-z0-9]{2,4}$")

SEARCH_TERMS = ("intern", "MBA")
PAGE_SIZE = 20  # the most the endpoint returns per request
MAX_PAGES_PER_SEARCH = 3  # 60 postings a search term; the run is close to its time limit

# Pacing (BR-004). All of it is bounded, because the run has a 60-minute limit.
REQUEST_PAUSE_SECONDS = 1.0         # between any two requests to Workday
DEFAULT_RETRY_WAIT_SECONDS = 10.0   # after a 429 that names no wait
MAX_RETRY_WAIT_SECONDS = 30.0       # the longest single wait we will sit through
RETRY_WAIT_SECONDS_PER_RUN = 90.0   # all such waits in a run share this
STOP_AFTER_REFUSED_READS = 3        # companies refused in a row, per data center, before we stop asking it

# Tests replace these two; nothing else should.
_sleep = time.sleep
_clock = time.monotonic


class _Pacing:
    """What this run knows about Workday's patience. One job process is one
    run, so module state is run state."""

    def __init__(self) -> None:
        self.last_request_at: float | None = None
        self.retry_wait_spent = 0.0
        # Companies refused in a row, by data center ("wd1", "wd5"). One
        # data center refusing us says nothing about another: on run 111 wd1
        # refused three companies while wd5 was still answering.
        self.refused_in_a_row: dict[str, int] = {}


_pacing = _Pacing()


def reset_pacing() -> None:
    global _pacing
    _pacing = _Pacing()


def _data_center(host: str) -> str:
    """`nvidia.wd5.myworkdayjobs.com` -> `wd5`."""
    labels = host.split(".")
    return labels[1] if len(labels) > 3 else host


def _wait_for_turn() -> None:
    if _pacing.last_request_at is not None:
        since = _clock() - _pacing.last_request_at
        if since < REQUEST_PAUSE_SECONDS:
            _sleep(REQUEST_PAUSE_SECONDS - since)
    _pacing.last_request_at = _clock()


def _request(api_url: str, body: dict, *, session) -> dict:
    """One request, at the run's pace. When Workday says slow down, wait as
    long as it asks (within limits) and send the same request once more. It
    is never sent a third time; the second refusal is the caller's to handle.
    """
    _wait_for_turn()
    try:
        return post_json(api_url, json_body=body, session=session)
    except FetchError as exc:
        if exc.status != 429:
            raise
        wait = DEFAULT_RETRY_WAIT_SECONDS if exc.retry_after is None else exc.retry_after
        wait = min(wait, MAX_RETRY_WAIT_SECONDS)
        if _pacing.retry_wait_spent + wait > RETRY_WAIT_SECONDS_PER_RUN:
            raise
        _pacing.retry_wait_spent += wait
        _sleep(wait)
    _wait_for_turn()
    return post_json(api_url, json_body=body, session=session)


def parse_site_link(url: str | None) -> tuple[str, str | None, str] | None:
    """(host, locale or None, site) from a Workday site link, or None when the
    link names no site (a bare host, a locale with nothing after it, or a page
    that isn't on Workday at all). Anything after the site name is ignored, so
    a link to one posting or a filtered search still gives its site.
    """
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not host.endswith(".myworkdayjobs.com"):
        return None
    segments = [p for p in parsed.path.split("/") if p]
    if not segments:
        return None
    if _LOCALE_RE.match(segments[0]):
        if len(segments) < 2:
            return None
        return host, segments[0], segments[1]
    return host, None, segments[0]


class _Refused(Exception):
    """Workday refused a request even after the wait. Carries where."""

    def __init__(self, term: str, page: int):
        self.term = term
        self.page = page


def _read_listing(api_url: str, company_name: str, *, session) -> tuple[dict[str, dict], _Refused | None, int]:
    """Every search, page by page, until done or refused. Returns the jobs by
    path, the refusal that stopped the read (or None), and how many requests
    were answered.
    """
    by_path: dict[str, dict] = {}
    answered = 0
    for term in SEARCH_TERMS:
        total = None
        for page in range(MAX_PAGES_PER_SEARCH):
            body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "searchText": term}
            try:
                data = _request(api_url, body, session=session)
            except FetchError as exc:
                if exc.status == 429:
                    return by_path, _Refused(term, page + 1), answered
                raise
            page_jobs = data.get("jobPostings") if isinstance(data, dict) else None
            if page_jobs is None:
                raise FetchError(f"{company_name}: unexpected Workday response shape")
            answered += 1
            if total is None:
                # Only the first page reports the real total; later pages say 0.
                total = data.get("total") or 0
            for job in page_jobs:
                key = job.get("externalPath") or f"{job.get('title')}|{job.get('locationsText')}"
                by_path.setdefault(key, job)
            if len(page_jobs) < PAGE_SIZE or (total and (page + 1) * PAGE_SIZE >= total):
                break
    return by_path, None, answered


def fetch(company: dict, *, session=None) -> list[FetchedPosting]:
    name = company.get("name") or ""
    address = company.get("feed_url") or company.get("careers_url") or ""
    parsed = parse_site_link(address)
    if parsed is None:
        raise FetchError(f"{name}: feed address isn't a Workday site link")
    host, locale, site = parsed
    data_center = _data_center(host)
    refused_before = _pacing.refused_in_a_row.get(data_center, 0)
    if refused_before >= STOP_AFTER_REFUSED_READS:
        # This data center keeps saying no. Asking again this run would only
        # be pushing at a closed door (BR-004.3).
        raise FetchError(
            f"{name}: not asked this run, because Workday's {data_center} answered HTTP 429 to the last "
            f"{refused_before} companies",
            status=429,
        )
    # The host's first label is the tenant. `feed_id` is only a fallback: it
    # can be stale (Wolters Kluwer is "wolterskluwer" on file, "wk" on Workday).
    tenant = host.split(".")[0] or company.get("feed_id") or ""
    api_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    site_base = f"https://{host}/{locale}/{site}" if locale else f"https://{host}/{site}"

    by_path, refused, answered = _read_listing(api_url, name, session=session)
    if refused is not None and answered == 0:
        _pacing.refused_in_a_row[data_center] = refused_before + 1
        raise FetchError(
            f'{api_url} returned HTTP 429 (search "{refused.term}", page {refused.page}, '
            f"after {answered} answered requests)",
            status=429,
        )
    # At least one request was answered, so the company has been read. A
    # refusal after that only makes a partial listing a little more partial.
    _pacing.refused_in_a_row[data_center] = 0
    if refused is not None:
        # The read counts as a success, so this is the only place it shows.
        print(
            f'workday: {name} was refused (HTTP 429) at search "{refused.term}", page {refused.page}, '
            f"after {answered} answered requests; kept the {len(by_path)} postings already read"
        )

    postings = []
    for job in by_path.values():
        external_path = job.get("externalPath", "")
        posted_on = job.get("postedOn", "") or ""
        posted_date_match = _ISO_DATE_RE.search(posted_on)
        postings.append(
            FetchedPosting(
                company_name=company.get("name", ""),
                title=job.get("title", ""),
                url=f"{site_base}{external_path}" if external_path else address,
                location_text=job.get("locationsText", ""),
                posted_date=posted_date_match.group(0) if posted_date_match else None,
                external_id=job.get("bulletFields", [None])[0] if job.get("bulletFields") else external_path,
                raw=job,
            )
        )
    return postings
