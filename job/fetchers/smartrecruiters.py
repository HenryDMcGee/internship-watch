"""SmartRecruiters' public postings API (no key required).

https://developers.smartrecruiters.com/docs/job-postings-api
"""
from __future__ import annotations

from job.fetchers.base import FetchError, get_json
from job.models import FetchedPosting

URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
APPLY_URL = "https://jobs.smartrecruiters.com/{company}/{posting_id}"


def fetch(company: dict, *, session=None) -> list[FetchedPosting]:
    identifier = company.get("feed_id")
    if not identifier:
        raise FetchError(f"{company.get('name')}: no SmartRecruiters company id on file")

    data = get_json(URL.format(company=identifier), session=session)
    content = data.get("content") if isinstance(data, dict) else None
    if content is None:
        raise FetchError(f"{company.get('name')}: unexpected SmartRecruiters response shape")

    postings = []
    for posting in content:
        location = posting.get("location") or {}
        location_text = ", ".join(
            part for part in [location.get("city"), location.get("region"), location.get("country")] if part
        )
        posting_id = posting.get("id", "")
        postings.append(
            FetchedPosting(
                company_name=company.get("name", ""),
                title=posting.get("name", ""),
                url=APPLY_URL.format(company=identifier, posting_id=posting_id),
                location_text=location_text,
                posted_date=(posting.get("releasedDate") or "")[:10] or None,
                external_id=posting_id,
                raw=posting,
            )
        )
    return postings
