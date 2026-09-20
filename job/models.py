"""The common posting model every fetcher returns.

Fetchers turn a company's feed (Greenhouse, Lever, Ashby, SmartRecruiters,
Workday, or Amazon.jobs) into a list of `FetchedPosting`, so the rest of the
job (matching, verification, storage) never needs to know which feed a
posting came from.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FetchedPosting:
    company_name: str
    title: str
    url: str
    location_text: str = ""
    posted_date: str | None = None  # ISO date, if the feed provides one
    external_id: str | None = None  # feed's own id for the posting, for de-duping
    raw: dict = field(default_factory=dict)  # original feed record, for debugging
