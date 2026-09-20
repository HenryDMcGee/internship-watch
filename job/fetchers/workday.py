"""Workday's unofficial CXS (career site) JSON API.

Not documented or officially supported by Workday, but widely relied on --
it's the same endpoint the public careers page itself calls. Derives the
tenant, host, and site name from the company's `careers_url`
(e.g. `https://nvidia.wd5.myworkdayjobs.com/en-US/NvidiaExternalCareerSite`)
since `feed_id` alone (the tenant) isn't enough to build the request.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from job.fetchers.base import FetchError, post_json
from job.models import FetchedPosting

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_careers_url(careers_url: str) -> tuple[str, str, str] | None:
    parsed = urlparse(careers_url)
    host = parsed.netloc
    tenant = host.split(".")[0] if host else ""
    segments = [p for p in parsed.path.split("/") if p]
    if not host or len(segments) < 2:
        return None
    locale, site = segments[0], segments[1]
    return host, locale, site


def fetch(company: dict, *, session=None) -> list[FetchedPosting]:
    careers_url = company.get("careers_url") or ""
    parsed = _parse_careers_url(careers_url)
    if parsed is None:
        raise FetchError(f"{company.get('name')}: careers_url isn't a full Workday site link")
    host, locale, site = parsed
    tenant = company.get("feed_id") or host.split(".")[0]

    api_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    data = post_json(api_url, json_body={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}, session=session)
    postings_raw = data.get("jobPostings") if isinstance(data, dict) else None
    if postings_raw is None:
        raise FetchError(f"{company.get('name')}: unexpected Workday response shape")

    postings = []
    for job in postings_raw:
        external_path = job.get("externalPath", "")
        posted_on = job.get("postedOn", "") or ""
        posted_date_match = _ISO_DATE_RE.search(posted_on)
        postings.append(
            FetchedPosting(
                company_name=company.get("name", ""),
                title=job.get("title", ""),
                url=f"https://{host}/{locale}{external_path}" if external_path else careers_url,
                location_text=job.get("locationsText", ""),
                posted_date=posted_date_match.group(0) if posted_date_match else None,
                external_id=job.get("bulletFields", [None])[0] if job.get("bulletFields") else external_path,
                raw=job,
            )
        )
    return postings
