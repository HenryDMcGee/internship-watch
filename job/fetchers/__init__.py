"""Registry of feed fetchers, and per-company error isolation.

`run_fetcher` never raises: a broken feed for one company is recorded and
skipped rather than stopping the run, per the plan ("Handle errors per
company, so one broken feed never stops the run.").
"""
from __future__ import annotations

from dataclasses import dataclass

from job.fetchers import amazon, ashby, greenhouse, lever, smartrecruiters, workday
from job.fetchers.base import FetchError
from job.models import FetchedPosting

FETCHERS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "workday": workday.fetch,
    "amazon_jobs": amazon.fetch,
}

# Feeds whose reader asks for a filtered or capped listing (see
# `workday.py`). A posting found in one is confirmed open; a posting missing
# from one proves nothing, so it must never be closed on that basis.
PARTIAL_LISTING_FEEDS = frozenset({"workday"})


def listing_is_complete(feed_type: str | None) -> bool:
    return feed_type not in PARTIAL_LISTING_FEEDS


@dataclass
class FetchResult:
    company_id: int
    company_name: str
    postings: list[FetchedPosting]
    error: str | None = None


def run_fetcher(company: dict, *, session=None) -> FetchResult:
    feed_type = company.get("feed_type")
    fetch = FETCHERS.get(feed_type)
    if fetch is None:
        return FetchResult(
            company_id=company["id"], company_name=company.get("name", ""), postings=[],
            error=f"no known feed type ({feed_type!r})" if feed_type else "no feed on file",
        )
    try:
        postings = fetch(company, session=session)
    except FetchError as exc:
        return FetchResult(company_id=company["id"], company_name=company.get("name", ""), postings=[], error=str(exc))
    except Exception as exc:  # noqa: BLE001 - any fetcher bug must not stop the run
        return FetchResult(company_id=company["id"], company_name=company.get("name", ""), postings=[], error=f"unexpected error: {exc}")
    return FetchResult(company_id=company["id"], company_name=company.get("name", ""), postings=postings)
