"""Mirror sites (Dreamwork, Simplify) as a tiebreaker, last in the verification order.

Only consulted when the feed, structured data, and browser checks all left
a posting unclear, and its answer never overrides an earlier definite one.
"""
from __future__ import annotations

import requests

from job.fetchers.base import USER_AGENT
from job.verify.text_signals import VerifyDecision, classify_text


def check_mirror(url: str, *, session=None, page_text: str | None = None) -> VerifyDecision:
    if page_text is None:
        session = session or requests
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        except requests.RequestException as exc:
            return VerifyDecision(decision="unclear", note=f"Could not load the mirror page: {exc}")
        if resp.status_code == 403:
            return VerifyDecision(decision="unclear", note="Mirror site blocked the request (403); not retrying or working around it.")
        if not resp.ok:
            return VerifyDecision(decision="unclear", note=f"Mirror site returned HTTP {resp.status_code}.")
        page_text = resp.text
    return classify_text(page_text)
