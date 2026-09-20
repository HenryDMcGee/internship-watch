"""Greenhouse's public job board API (no key required).

https://developers.greenhouse.io/job-board.html#list-jobs
"""
from __future__ import annotations

from job.fetchers.base import FetchError, get_json
from job.models import FetchedPosting

URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


def fetch(company: dict, *, session=None) -> list[FetchedPosting]:
    board = company.get("feed_id")
    if not board:
        raise FetchError(f"{company.get('name')}: no Greenhouse board id on file")

    data = get_json(URL.format(board=board), params={"content": "true"}, session=session)
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if jobs is None:
        raise FetchError(f"{company.get('name')}: unexpected Greenhouse response shape")

    postings = []
    for job in jobs:
        location = (job.get("location") or {}).get("name", "")
        postings.append(
            FetchedPosting(
                company_name=company.get("name", ""),
                title=job.get("title", ""),
                url=job.get("absolute_url", ""),
                location_text=location,
                posted_date=(job.get("first_published") or job.get("updated_at") or "")[:10] or None,
                external_id=str(job.get("id")) if job.get("id") is not None else None,
                raw=job,
            )
        )
    return postings
