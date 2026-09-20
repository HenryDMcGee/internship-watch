"""Ashby's public job board API (no key required).

https://developers.ashbyhq.com/reference/jobpostingapi
"""
from __future__ import annotations

from job.fetchers.base import FetchError, get_json
from job.models import FetchedPosting

URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"


def fetch(company: dict, *, session=None) -> list[FetchedPosting]:
    board = company.get("feed_id")
    if not board:
        raise FetchError(f"{company.get('name')}: no Ashby board name on file")

    data = get_json(URL.format(board=board), params={"includeCompensation": "false"}, session=session)
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if jobs is None:
        raise FetchError(f"{company.get('name')}: unexpected Ashby response shape")

    postings = []
    for job in jobs:
        posted_date = job.get("publishedDate")
        postings.append(
            FetchedPosting(
                company_name=company.get("name", ""),
                title=job.get("title", ""),
                url=job.get("jobUrl") or job.get("applyUrl", ""),
                location_text=job.get("location", ""),
                posted_date=posted_date[:10] if posted_date else None,
                external_id=job.get("id"),
                raw=job,
            )
        )
    return postings
