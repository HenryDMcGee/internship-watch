"""Lever's public postings API (no key required).

https://github.com/lever/postings-api
"""
from __future__ import annotations

from datetime import datetime, timezone

from job.fetchers.base import FetchError, get_json
from job.models import FetchedPosting

URL = "https://api.lever.co/v0/postings/{account}"


def fetch(company: dict, *, session=None) -> list[FetchedPosting]:
    account = company.get("feed_id")
    if not account:
        raise FetchError(f"{company.get('name')}: no Lever account id on file")

    data = get_json(URL.format(account=account), params={"mode": "json"}, session=session)
    if not isinstance(data, list):
        raise FetchError(f"{company.get('name')}: unexpected Lever response shape")

    postings = []
    for posting in data:
        categories = posting.get("categories") or {}
        posted_date = None
        created_at = posting.get("createdAt")
        if isinstance(created_at, (int, float)):
            posted_date = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc).date().isoformat()
        postings.append(
            FetchedPosting(
                company_name=company.get("name", ""),
                title=posting.get("text", ""),
                url=posting.get("hostedUrl", ""),
                location_text=categories.get("location", ""),
                posted_date=posted_date,
                external_id=posting.get("id"),
                raw=posting,
            )
        )
    return postings
