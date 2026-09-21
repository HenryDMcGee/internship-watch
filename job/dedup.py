"""Matches a newly found posting (from a company feed or Claude discovery)
against postings already in D1, so the same role never lands on the
dashboard twice under two different rows.

A URL match is checked first since it's exact, but a URL alone isn't
enough -- issue #26 saw a company re-list an already-known role under a
new URL, which discovery then saved as a second, brand-new posting
alongside the original Henry had already marked applied. Company +
normalized title is the fallback for exactly that case.
"""
from __future__ import annotations

from job.matching import normalize_title

from companies import normalize_company_name


async def find_duplicate_posting(db, *, company_name: str, title: str, url: str | None) -> dict | None:
    if url:
        existing = await db.first("SELECT id FROM postings WHERE url = ?", url)
        if existing:
            return existing

    normalized_company = normalize_company_name(company_name or "")
    normalized_title = normalize_title(title or "")
    if not normalized_company or not normalized_title:
        return None

    candidates = await db.all("SELECT id, company_name, title FROM postings")
    for candidate in candidates:
        if (
            normalize_company_name(candidate.get("company_name") or "") == normalized_company
            and normalize_title(candidate.get("title") or "") == normalized_title
        ):
            return candidate
    return None
