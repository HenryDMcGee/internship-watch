"""Amazon.jobs' public search JSON endpoint (no key required).

Amazon's own postings all live on amazon.jobs rather than one of the
standard ATS providers, so this is the one fetcher that searches instead of
listing a single company's board.
"""
from __future__ import annotations

from datetime import datetime

from job.fetchers.base import FetchError, get_json
from job.models import FetchedPosting

URL = "https://www.amazon.jobs/en/search.json"
BASE = "https://www.amazon.jobs"


def _parse_posted_date(text: str) -> str | None:
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def fetch(company: dict, *, session=None, query: str = "MBA intern") -> list[FetchedPosting]:
    data = get_json(URL, params={"base_query": query, "result_limit": 100, "offset": 0}, session=session)
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if jobs is None:
        raise FetchError("amazon.jobs: unexpected response shape")

    postings = []
    for job in jobs:
        job_path = job.get("job_path", "")
        postings.append(
            FetchedPosting(
                company_name=job.get("company_name") or company.get("name", "Amazon"),
                title=job.get("title", ""),
                url=f"{BASE}{job_path}" if job_path else BASE,
                location_text=job.get("normalized_location", ""),
                posted_date=_parse_posted_date(job.get("posted_date", "")),
                external_id=job.get("id_icims") or job.get("job_path"),
                raw=job,
            )
        )
    return postings
