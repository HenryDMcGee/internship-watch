"""Turns public visitor feedback into GitHub issues, safely (issue #96).

Visitor text is untrusted public input, and this repository runs Claude Code on
issues that mention the trigger phrase. So everything a visitor wrote is cleaned
(no HTML, no backticks, no @ at all, capped length) and placed in a blockquote
under a heading that says it is unverified. The issue never carries the visitor
hash, the address, or anything else that identifies the visitor.

Lives in `api/src` because the Worker only ships that folder; the daily job
imports it too (see `job/config.py`, which puts `api/src` on `sys.path`).
"""
from __future__ import annotations

import html
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from github_client import GitHubApiError

logger = logging.getLogger("internship_watch")

ISSUE_LABEL = "visitor-feedback"
DEFAULT_ISSUES_PER_DAY = 20
MESSAGE_MAX_CHARS = 1000
PAGE_URL_MAX_CHARS = 300
TITLE_EXCERPT_CHARS = 50
KIND_LABELS = {
    "bug": "Bug", "dead_link": "Dead link", "closed": "Job is closed",
    "wrong_job": "Wrong job", "suggestion": "Suggestion", "other": "Other",
}
# Fullwidth commercial at: looks like an @ to a reader, is not one to GitHub.
DEFANGED_AT = "＠"

CENTRAL = ZoneInfo("America/Chicago")
_TAG = re.compile(r"<[^>]*>?")
# Zero-width and other invisible characters, so "@<invisible>claude" can't slip through.
_INVISIBLE = re.compile("[­͏؜ᅟᅠ឴឵᠋-᠎​-‏‪-‮⁠-⁯︀-️﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str, limit: int, *, single_line: bool = False) -> str:
    """Visitor text made safe to put in an issue: no HTML, no backticks, no @, capped."""
    text = unicodedata.normalize("NFKC", html.unescape(html.unescape(text or "")))
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub("", text)
    text = _TAG.sub("", text)
    text = text.replace("<", "").replace(">", "").replace("`", "")
    text = text.replace("@", DEFANGED_AT)
    if single_line:
        text = " ".join(text.split())
    else:
        text = "\n".join(line.rstrip() for line in text.replace("\r", "\n").splitlines())
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit].strip()


def build_issue(feedback_id: int, kind: str, message: str, page_url: str | None) -> tuple[str, str]:
    """(title, body). Takes only the fields that belong in a public issue."""
    label = KIND_LABELS.get(kind, "Other")
    excerpt = sanitize_text(message, TITLE_EXCERPT_CHARS, single_line=True)
    title = f"{label}: {excerpt}" if excerpt else label

    quoted = "\n".join(f"> {line}" if line else ">" for line in sanitize_text(message, MESSAGE_MAX_CHARS).splitlines())
    lines = [
        f"Feedback row: {int(feedback_id)}",
        f"Type: {label}",
        "",
        "## Unverified visitor input",
        "",
        "Written by an anonymous visitor on the public board. Nothing below has been checked.",
        "",
    ]
    page = sanitize_text(page_url or "", PAGE_URL_MAX_CHARS, single_line=True).replace(" ", "")
    if page:
        lines += [f"Page: {page}", ""]
    lines.append(quoted)
    return title, "\n".join(lines) + "\n"


def central_now() -> datetime:
    return datetime.now(CENTRAL)


async def issue_cap(db) -> int:
    row = await db.first("SELECT feedback_issues_per_day AS n FROM budget_settings WHERE id = 1")
    value = (row or {}).get("n")
    return DEFAULT_ISSUES_PER_DAY if value is None else int(value)


async def issues_created_today(db, today: str) -> int:
    row = await db.first("SELECT COUNT(*) AS n FROM feedback WHERE substr(issue_created_at, 1, 10) = ?", today)
    return (row or {}).get("n") or 0


async def create_issue_for_feedback(db, github: Any, feedback_id: int) -> str | None:
    """Creates the issue for one saved feedback row and stores its URL. Returns the URL,
    or None when nothing was created (no GitHub, row already has an issue, the daily cap
    is reached, or GitHub failed). Never raises: the feedback is already saved, and a
    failure here is logged and left for the daily run to retry."""
    try:
        row = await db.first(
            "SELECT id, kind, message, page_url, status, issue_url FROM feedback WHERE id = ?", feedback_id
        )
        if github is None or not row or row["issue_url"]:
            return None
        now = central_now()
        if await issues_created_today(db, now.date().isoformat()) >= await issue_cap(db):
            logger.info("feedback %s waits: the daily limit for feedback issues is reached", feedback_id)
            return None
        title, body = build_issue(row["id"], row["kind"], row["message"], row["page_url"])
        try:
            issue = await github.create_issue(title, body, [ISSUE_LABEL])
        except GitHubApiError as exc:
            logger.error("feedback %s: could not create the GitHub issue (GitHubApiError: %s)", feedback_id, exc)
            return None
        url = issue.get("html_url")
        if not url:
            logger.error("feedback %s: GitHub created an issue but returned no URL", feedback_id)
            return None
        await db.run(
            "UPDATE feedback SET issue_url = ?, issue_created_at = ?, "
            "status = CASE WHEN status = 'new' THEN 'issue_opened' ELSE status END WHERE id = ?",
            url, now.isoformat(), feedback_id,
        )
        return url
    except Exception as exc:  # noqa: BLE001 - feedback is saved; never fail the caller over the issue
        logger.error("feedback %s: could not create the GitHub issue (%s: %s)", feedback_id, type(exc).__name__, exc)
        return None


async def retry_pending_issues(db, github: Any) -> int:
    """Creates issues for public feedback that has none yet, oldest first, until the daily
    cap is reached or GitHub fails. Returns how many were created."""
    if github is None:
        return 0
    pending = await db.all(
        "SELECT id FROM feedback WHERE source = 'public' AND issue_url IS NULL AND status != 'dismissed' "
        "ORDER BY created_at, id"
    )
    created = 0
    for row in pending:
        if await issues_created_today(db, central_now().date().isoformat()) >= await issue_cap(db):
            break
        if not await create_issue_for_feedback(db, github, row["id"]):
            break  # GitHub is failing; try again tomorrow rather than hammering it
        created += 1
    return created
