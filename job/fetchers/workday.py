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
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from job.fetchers.base import FetchError, post_json
from job.models import FetchedPosting

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# en-US, ja-JP, zh-CN, es-419. A site name is never shaped like this.
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Za-z0-9]{2,4}$")

SEARCH_TERMS = ("intern", "MBA")
PAGE_SIZE = 20  # the most the endpoint returns per request
MAX_PAGES_PER_SEARCH = 3  # 60 postings a search term; the run is close to its time limit


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


def _search(api_url: str, term: str, company_name: str, *, session) -> list[dict]:
    jobs: list[dict] = []
    total = None
    for page in range(MAX_PAGES_PER_SEARCH):
        data = post_json(
            api_url,
            json_body={"appliedFacets": {}, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "searchText": term},
            session=session,
        )
        page_jobs = data.get("jobPostings") if isinstance(data, dict) else None
        if page_jobs is None:
            raise FetchError(f"{company_name}: unexpected Workday response shape")
        if total is None:
            # Only the first page reports the real total; later pages say 0.
            total = data.get("total") or 0
        jobs.extend(page_jobs)
        if len(page_jobs) < PAGE_SIZE or (total and (page + 1) * PAGE_SIZE >= total):
            break
    return jobs


def fetch(company: dict, *, session=None) -> list[FetchedPosting]:
    address = company.get("feed_url") or company.get("careers_url") or ""
    parsed = parse_site_link(address)
    if parsed is None:
        raise FetchError(f"{company.get('name')}: feed address isn't a Workday site link")
    host, locale, site = parsed
    # The host's first label is the tenant. `feed_id` is only a fallback: it
    # can be stale (Wolters Kluwer is "wolterskluwer" on file, "wk" on Workday).
    tenant = host.split(".")[0] or company.get("feed_id") or ""
    api_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    site_base = f"https://{host}/{locale}/{site}" if locale else f"https://{host}/{site}"

    by_path: dict[str, dict] = {}
    for term in SEARCH_TERMS:
        for job in _search(api_url, term, company.get("name") or "", session=session):
            key = job.get("externalPath") or f"{job.get('title')}|{job.get('locationsText')}"
            by_path.setdefault(key, job)

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
