"""Structured page data: schema.org JobPosting JSON-LD, if the page has it.

Second in the verification order, after the company's own data feed.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

import requests

from job.fetchers.base import USER_AGENT
from job.verify.text_signals import VerifyDecision

_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)


def _iter_job_postings(html: str):
    for match in _LD_JSON_RE.finditer(html):
        try:
            data = json.loads(match.group(1).strip())
        except ValueError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "JobPosting":
                yield item
            elif "@graph" in item and isinstance(item["@graph"], list):
                for sub in item["@graph"]:
                    if isinstance(sub, dict) and sub.get("@type") == "JobPosting":
                        yield sub


def _parse_date(text: str) -> date | None:
    if not text:
        return None
    cleaned = text.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def classify_job_posting(job_posting: dict, *, today: date | None = None) -> VerifyDecision:
    today = today or date.today()
    valid_through = _parse_date(job_posting.get("validThrough", ""))
    if valid_through is not None:
        if valid_through < today:
            return VerifyDecision(decision="closed", note=f"validThrough {valid_through.isoformat()} has passed.")
        return VerifyDecision(decision="open", note=f"validThrough {valid_through.isoformat()} is still ahead.")
    return VerifyDecision(decision="open", note="schema.org JobPosting found with no expiration listed.")


def check_structured_data(url: str, *, session=None, html: str | None = None) -> VerifyDecision:
    """Fetch `url` (unless `html` is given, for tests) and check for schema.org data."""
    if html is None:
        session = session or requests
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        except requests.RequestException as exc:
            return VerifyDecision(decision="unclear", note=f"Could not load the page: {exc}")
        if resp.status_code == 404:
            return VerifyDecision(decision="closed", note="Page returned 404.")
        if resp.status_code == 403:
            return VerifyDecision(decision="unclear", note="Page blocked the request (403); not retrying or working around it.")
        if not resp.ok:
            return VerifyDecision(decision="unclear", note=f"Page returned HTTP {resp.status_code}.")
        html = resp.text

    postings = list(_iter_job_postings(html))
    if not postings:
        return VerifyDecision(decision="unclear", note="No schema.org JobPosting data on the page.")
    return classify_job_posting(postings[0])
