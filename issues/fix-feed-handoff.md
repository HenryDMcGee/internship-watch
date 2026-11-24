# Spec: fix the Workday feed route and the scout handoff

| | |
| --- | --- |
| Implements | `docs/requirements/BR-001-feed-coverage.md` |
| Status | Approved by Henry on 2026-11-23 ("perform code changes based on this") |

Every acceptance criterion names the business requirement it serves. Where the build
differs from the first draft of this spec, the spec was changed first; those places are
marked **Changed**.


## Summary

The feed route reads `companies.feed_type` and `companies.careers_url`. The scout writes
there, and it also writes a side log of `trial` rows in `sources` that nothing appears to
read. The handoff is broken in three places, and the worst one is that the Workday route
has returned nothing for any of its 37 companies since at least 2026-11-16.

Everything in "What the database shows" was measured in D1 on 2026-11-23. Treat it as
given.

## What the database shows

### Workday: 37 companies, every check fails

- `run_stats` has 37 `"<Company> (workday)"` checks per run. From run 97 to run 109 they
  returned 0 jobs in total.
- Across runs 15 to 109, only four Workday companies ever returned jobs: Visa, Cadence
  Design Systems, Tokyo Electron and Monolithic Power Systems.
- All 37 have `last_feed_status = 'error'` with
  `"<Company>: careers_url isn't a full Workday site link"`.

They fall into two groups.

**Group A, 19 companies, `careers_url` is already a `myworkdayjobs.com` site link and
still fails:**

Autodesk, Broadcom, Cadence Design Systems, CrowdStrike, Entegris, Fiserv,
GlobalFoundries, HP Inc., KLA, Lam Research, Marvell Technology, Microchip Technology,
NVIDIA, PayPal, Seagate Technology, Snap, Workday, Zebra Technologies, Zillow.

Examples that fail today: `https://autodesk.wd1.myworkdayjobs.com/uni`,
`https://zebra.wd1.myworkdayjobs.com/Zebra_Careers`,
`https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite`.

**Group B, 18 companies, `careers_url` is a marketing page or another site:**

| Company | Current `careers_url` | Workday URL found in `sources` (trial) |
| --- | --- | --- |
| Analog Devices | analog.com students page | `https://analogdevices.wd1.myworkdayjobs.com/External` |
| Applied Materials | appliedmaterials.com students page | `https://amat.wd1.myworkdayjobs.com/External` |
| Equinix | careers.equinix.com/internships | `https://equinix.wd1.myworkdayjobs.com/External` |
| F5 | f5.com early careers | `https://ffive.wd5.myworkdayjobs.com/f5jobs` |
| Keysight | careers.keysight.com students page | `https://keysight.wd5.myworkdayjobs.com/Keysight_Careers` |
| Micron | micron.com university recruiting | `https://micron.wd1.myworkdayjobs.com/External` |
| Nissan | a Handshake employer page | `https://nissan.wd1.myworkdayjobs.com/Nissan_Careers` |
| Onto Innovation | ontoinnovation.com/careers | `https://ontoinnovation.wd1.myworkdayjobs.com/en-US/Careers` |
| Qualcomm | qualcomm.com campus page | `https://qualcomm.wd5.myworkdayjobs.com/External` |
| Salesforce | salesforce.com internships page | `https://salesforce.wd1.myworkdayjobs.com/Futureforce_Internships` |
| Supermicro | jobs.supermicro.com | `https://supermicro.wd1.myworkdayjobs.com/Supermicro` |
| Symbotic | symbotic.com open positions | `https://symbotic.wd1.myworkdayjobs.com/Symbotic` |
| Tokyo Electron | tel.com/careers | `https://tel.wd3.myworkdayjobs.com/en-US/TEL-Careers` |
| Wolters Kluwer | careers.wolterskluwer.com | `https://wk.wd3.myworkdayjobs.com/External` |
| Fair Isaac (FICO) | fico.com careers | none found |
| Fidelity National Information Services | careers.fisglobal.com | none found |
| Monolithic Power Systems | monolithicpower.com careers | none found |
| Samsung Electronics | samsung.com internships | none found |

The trial URLs above had `?q=intern` query strings in some cases; they are shown here
without them.

### Six feeds return 404 on every run

| Feed | Failing address |
| --- | --- |
| greenhouse | `boards-api.greenhouse.io/v1/boards/cerebrassystems/jobs` |
| greenhouse | `boards-api.greenhouse.io/v1/boards/circle/jobs` |
| greenhouse | `boards-api.greenhouse.io/v1/boards/etsy/jobs` |
| greenhouse | `boards-api.greenhouse.io/v1/boards/jdcom/jobs` |
| greenhouse | `boards-api.greenhouse.io/v1/boards/unity3d/jobs` |
| ashby | `api.ashbyhq.com/posting-api/job-board/rippling` |

JD.com should not be in `companies` at all. China-based companies are excluded.

### The `sources` table

| Observation | Value |
| --- | --- |
| `sources` rows with `status='trial'` | 963, all `discovered_by: claude-scout` |
| Trial rows older than 30 days | 622 |
| Trial rows pointing at an ATS board | 156 (94 Workday, 47 Greenhouse, 8 SmartRecruiters, 4 Lever, 3 Ashby) |
| Trial rows ever promoted | 0 |
| `sources.found_count` on trial | 0 on all 963 |
| `sources.last_success_at` | NULL on every row, active included |
| `source_checks` rows, ever | 0 |
| `run_stats.source_id` | NULL on all rows; `run_stats` is keyed by company and provider |
| `sources` rows with `status='active'` | 30, September seed data, mostly `last_checked` 2026-09-19 |

The same page appears several times under slightly different URLs, for example
`jobs.adp.com/`, `jobs.adp.com`, `jobs.adp.com/en/jobs/` and `jobs.adp.com/en/jobs`.
The scout has also added Alibaba, JD.com, Baidu and Foxconn pages.

## Step 1 — confirm in code, report, then continue

Answer these in the pull request, each with the file and function that shows it.

1. Why does a well-formed link such as `https://zebra.wd1.myworkdayjobs.com/Zebra_Careers`
   produce "careers_url isn't a full Workday site link"? Cadence returned jobs earlier in
   the project and fails now, so say whether this is a regression and what changed.
2. Which step overwrites `companies.careers_url`, and does it know the company's
   `feed_type` when it does?
3. Does anything read `sources` rows with `status='trial'`, or read `sources` at all,
   other than the dashboard's source list?
4. Is there any writer for `source_checks`, `sources.found_count` or
   `sources.last_success_at`?

If the answer to 1 or 2 contradicts the plan below, stop and say so before changing
anything.

## Step 2 — the fixes

### Functionality

- Workday companies return jobs again through the feed route.
- A company's feed address is no longer lost when its human-facing careers page changes.
- Feeds that fail repeatedly are visible on the dashboard instead of failing silently.
- A trial source that is not adopted within 30 days is retired, and the scout stops
  proposing pages it has already proposed.

### Technical changes

**Workday parser.** Fix whatever rejects Group A. Add tests using the three example URLs
above, plus one with a locale segment (`/en-US/Careers`) and one with a query string.

**Separate the feed address from the careers page.** `careers_url` is doing two jobs: the
address the Workday fetcher parses, and the page careers extraction reads. Give the feed
its own field (a new column with a migration, or the existing `feed_id` if it can hold
it) and make the scout unable to overwrite it with a non-feed URL.

**Repair Group B.** Set the feed address for the 14 companies in the table from the URL
listed. Verify each returns jobs before saving it. For the four with none found, leave
`feed_type` as it is only if a working address can be found; otherwise clear `feed_type`
so they fall back to careers extraction and discovery instead of erroring every run.

**Bad board names.** A feed that returns HTTP 404 for the configured number of runs in a
row is cleared automatically, so the company falls back to the scout and the careers-page
reader, and the dead address is remembered so it is not adopted again. China-based
companies are marked `removed`, and the quarterly ranking no longer adds them.

**Changed:** the draft said to correct the six board names by hand. A general rule
replaces the one-off repair, because board names will go stale again.

**Partial listings.** The Workday reader asks for internship and MBA postings only, a few
pages at most, so that reading 37 large companies does not add to run time. A posting
missing from that partial list is not treated as closed; only a posting found in it is
confirmed open. Without this, turning Workday back on would mark every posting found
another way as closed.

**Feed health.** After N consecutive failed checks (make N a setting, default 5), mark the
feed as failing and show it on the dashboard. Do not keep checking a failing feed every
run without telling anyone.

**Harvest trial before retiring it.** For each of the 156 ATS-board trial rows, if the
company has no working feed, test the URL and adopt it as the company's feed if it
returns jobs.

**Retirement rule.** A `trial` row older than 30 days that was not adopted as a company's
feed becomes `status='demoted'`, with the date and reason in `notes`. Demoted, not
deleted. "Graduated" means adopted as a company's feed after a successful read. Do not
use `found_count`; it has no writer.

**Changed:** the draft said `retired`. The `sources.status` column only allows `trial`,
`active` and `demoted`, and `demoted` already means "cut automatically" in the scout's
design, so the existing status is used and no table rebuild is needed. The draft also
said a feed must "return jobs" to be adopted. A correct feed can list no internships in
the off-season, so the test is a successful read instead.

**Scout hygiene.** Before inserting, compare the URL against existing trial and demoted
rows on a normalized form (lowercase host, no trailing slash, no query or fragment). The
four ADP variants are the test case. Apply the same exclusion that keeps China-based
companies out of `companies`.

**Dead pieces.** Based on the answers to questions 3 and 4, remove writers and readers
that can never run, and say what was removed. Do not drop a table the dashboard reads.

### Architecture

The feed configuration lives on `companies`, and that is the only handoff that matters.
The `sources` table becomes what it already is in practice: a log of what the scout
proposed, with a lifecycle of trial, adopted (`active`) or demoted. Graduation is defined by
adoption into the feed configuration, which is observable, instead of by a counter that
was never written.

## Acceptance criteria

1. **[process]** The four questions in step 1 are answered with file and function references.
2. **[BR-001.1]** A run after the change shows Workday checks returning jobs for Group A companies.
3. **[BR-001.1]** The 14 Group B companies with a listed URL have a working feed address, each verified
   before saving.
4. **[BR-001.1]** No feed returns 404 on every run; each of the six is corrected or cleared.
5. **[BR-001.4]** JD.com is not in `companies`, and the scout does not propose China-based companies.
6. **[BR-001.2]** A failing feed is shown on the dashboard after the configured number of failures.
7. **[BR-001.3]** The scout cannot overwrite a feed address with a non-feed URL. There is a test for it.
8. **[BR-001.5]** Trial rows older than 30 days and not adopted are `demoted`. The count is reported in
   the run summary.
9. **[BR-001.5]** The scout does not insert a URL that matches an existing trial or demoted row on
   normalized form.
10. **[process]** `pytest` and `python scripts/validate_titles.py` pass. Both frontend suites stay at or
    above 80% on statements, branches, functions and lines.

## Out of scope

- Run time against the 60-minute Actions limit. It gets its own issue once this lands,
  because working Workday feeds will change the numbers.
- The 235 postings from `claude-discovery` and `careers_page` with `verified_at` null.
- Careers page snapshots that report `changed` on most checks (114 of 175).
- The `excluded word 'marketing'` rule.
