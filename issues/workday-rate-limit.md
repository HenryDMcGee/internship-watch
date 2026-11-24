# Slow down when a site asks, and never demote a source on a temporary refusal

Implements `docs/requirements/BR-004-rate-limits.md`. Every acceptance criterion names
the requirement it serves. Where the build departs from this text, this text is changed
first and the change is marked **Changed**.

## What the database shows

Measured in D1 during run 111 on 2026-11-24.

- 19 Workday companies have a site address in `companies.feed_url`. 2 read (Fiserv 74
  postings, Microchip 71). 17 have
  `last_feed_error = "https://<host>/wday/cxs/<tenant>/<site>/jobs returned HTTP 429"`.
- Run 110, three hours earlier: all 19 refused.
- The reader sends up to 6 requests per company (2 searches, up to 3 pages each) with no
  pause, and reads about a dozen Workday companies inside the same minute.
- The refusals are on both `wd1` and `wd5` hosts. Fiserv and Microchip, both `wd5`, read
  early in the feed phase; every `wd5` company after them was refused.
- The error does not say which request was refused, so "refused at once" and "refused on
  page 2" cannot be told apart today.
- 18 Workday companies have no site address and are waiting for a trial address to be
  tried. Two Workday feeds have now read, so the hold on those tries lifts on the next
  run (`sources_lifecycle.unproven_feed_types`).
- `sources_lifecycle.adopt_trial_feeds` demotes a trial address on any failed try,
  whatever the error.
- `sources_lifecycle._wants_a_feed` lets a feed be replaced after 5 failed reads in a
  row, whatever the error. The 17 refused feeds are at 2.

## The fixes

### Functionality

- Workday feeds are read at a steady pace, and a "too many requests" answer is waited
  out and tried again once.
- A company whose later pages are refused keeps the postings already read.
- A trial address whose try is refused, times out or meets a server error stays on trial
  and is tried again on a later run. Only "not found" demotes it.
- A feed that keeps being refused is not replaced by another address on that account.
- The run summary says how many trial addresses could not be judged, and the feed error
  says which request was refused.

### Technical changes

**Carry the refusal's details.** `FetchError` gains `status` and `retry_after`, set by
`get_json` and `post_json`. `Retry-After` is read as seconds; a date or anything
unreadable is treated as absent.

**Pace Workday requests.** At least `REQUEST_PAUSE_SECONDS` (1.0) between any two
requests to Workday in a run, across companies, since the companies share Workday's
hosts.

**Wait and try once more.** On HTTP 429, wait `Retry-After` seconds, or
`DEFAULT_RETRY_WAIT_SECONDS` (10) when the site names none, never more than
`MAX_RETRY_WAIT_SECONDS` (30), then send the same request once more. All such waiting in
a run shares one allowance, `RETRY_WAIT_SECONDS_PER_RUN` (90). When it is used up, a 429
fails at once.

**Keep what was read.** If a request is still refused after the retry and the company
already has postings from an earlier page or search, stop and return those. The listing
is partial by design (`PARTIAL_LISTING_FEEDS`), so nothing downstream reads a missing
posting as closed. If nothing has been read, the read fails with an error that names the
search, the page, and how many requests had succeeded.

**Stop asking.** After `STOP_AFTER_REFUSED_READS` (3) companies in a row end refused,
the remaining Workday companies are not asked in this run. Each fails with an error that
says so and contains "HTTP 429", so it is classed as a refusal everywhere else. A
successful read resets the count.

**A refused try is not a verdict.** In `adopt_trial_feeds`, only an error that
`is_dead_address_error` accepts (HTTP 404) demotes the source. Any other error leaves it
on trial, counts it in a new `AdoptionOutcome.undecided`, and reports it with the untried
addresses so the 30-day rule does not demote it in the same run. After a 429, no further
address on that platform is tried in the run.

**A refused feed is not a failing address.** `_wants_a_feed` does not treat a feed as
replaceable while its last error is a rate limit, however many runs in a row.

**Say so.** The run summary gains one sentence when `undecided` is above zero.

### Architecture

No new service, secret, table or column. Pacing and waiting live in the Workday reader
as per-run state in the job's process. The waiting is bounded: about one second per
Workday request (around 2 minutes for 19 companies, up to 4 for 37) plus at most 90
seconds of retry waits.

## Acceptance criteria

1. [BR-004.1] Two Workday requests in a run are never sent less than
   `REQUEST_PAUSE_SECONDS` apart, including across companies.
2. [BR-004.1] A 429 is waited out for the time the site names (capped) and the request
   is sent once more; a read that succeeds on the second try returns its postings.
3. [BR-004.1] A company refused on a later page or search returns the postings already
   read, and the read counts as successful.
4. [BR-004.2] A trial address whose try ends in 429, a timeout or a 5xx stays `trial`.
   Only a 404 demotes it.
5. [BR-004.2] An address left undecided is not demoted by the 30-day rule in the same
   run.
6. [BR-004.2] A feed whose last error is a rate limit is not replaced by a trial
   address, at any number of failed runs.
7. [BR-004.3] The same request is never sent more than twice, and total retry waiting
   in a run never exceeds `RETRY_WAIT_SECONDS_PER_RUN`.
8. [BR-004.3] After three refused companies in a row, no further Workday request is sent
   in the run, and after a 429 on a trial address no further address on that platform is
   tried in the run.
9. [BR-004.4] A refused read's error names the search, the page and the number of
   requests that had succeeded; the run summary reports undecided trial addresses.
10. [process] `pytest` and `python scripts/validate_titles.py` pass.

## Out of scope

- Run time against the 60-minute limit (candidate BR-002).
- Reading Workday from anywhere other than GitHub's runners.
- Pacing for the other feed platforms. None has refused us.

## What the first run after merge will tell us

The 429 errors will name the request. "After 0 requests" on every company, with the
stop rule firing, means Workday is refusing GitHub's runners and pacing cannot help.
"After 1 or more" means the limit is on speed and the pause can be tuned.
