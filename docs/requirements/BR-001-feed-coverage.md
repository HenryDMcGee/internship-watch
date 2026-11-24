# BR-001 — Tracked companies are read through their job feeds, and failures are visible

| | |
| --- | --- |
| Status | **Approved** by Henry, 2026-11-23 |
| Owner | Henry McGee |
| Written | 2026-11-23 |
| Evidence | D1 queries against `internship-watch-db`, 2026-11-23 |
| Spec that implements it | `issues/fix-feed-handoff.md` |

A business requirement says what the product must achieve and how we will know, in
terms a user would recognize. It names no files, tables or code. Those belong in the
spec and the plan, which must trace back to the numbered items below.

---

## 1. The business need

Internship Watch exists so that a Summer 2027 MBA internship at a tracked company is seen
the day it is posted. The persona in `docs/persona-public-board.md` puts it plainly: the
user's fear is the posting she never saw.

Today the product cannot make that promise for a large part of its company list.

- 94 of the 236 tracked companies are set up to be read through a structured job feed.
  **43 of those 94 fail on every run**, so 54% of feeds are healthy.
- All 37 companies on the Workday feed fail, including NVIDIA, Salesforce, Qualcomm,
  Micron, HP, PayPal and Snap. None has returned a job through its feed since at least
  2026-11-16.
- These companies are still covered by web-search discovery, which costs more (about 93%
  of the roughly $1.92 spent per run) and has no guarantee of seeing every posting.
- **Nothing told the owner.** The failures were found by querying the database by hand.

The cost of this is missed or late postings at the companies most likely to matter, and
no way to know it is happening.

## 2. Who this is for

| Stakeholder | What they need from this |
| --- | --- |
| Henry, as the primary user | Confidence that a posting at a tracked company will be seen |
| Henry, as the owner | To be told when part of the system stops working |
| Classmates using the public board | A board that is complete enough to trust |

## 3. Requirements

Each item is a statement that can be checked as true or false.

**BR-001.1 — Feed coverage.**
Every tracked company that has a job feed is read through that feed on each daily run.

**BR-001.2 — No silent failures.**
When a company's feed stops working, the owner can see that on the dashboard within a
set number of runs, without querying the database.

**BR-001.3 — Feeds stay working.**
Routine updates to a company's information, such as a new careers page address, do not
break a feed that was working.

**BR-001.4 — Exclusions are honored everywhere.**
A company the owner has excluded, including every China-based company, does not appear in
the tracked list or among newly proposed sources.

**BR-001.5 — Proposed sources prove themselves or leave.**
A newly proposed source that has not produced a real posting within 30 days is retired,
and the same page is not proposed again.

## 4. How we will know

Baselines were measured on 2026-11-23. **The targets are proposals for you to set.**

| Measure | Requirement | Today | Target |
| --- | --- | --- | --- |
| Share of feed-configured companies with a successful check per run | BR-001.1 | 54% (51 of 94) | 95% or higher |
| Workday companies returning jobs per run | BR-001.1 | 0 of 37 | Every company still set up as Workday |
| Runs before a broken feed is shown to the owner | BR-001.2 | Never shown | 5 runs or fewer |
| Working feeds broken by a routine update | BR-001.3 | 18 feeds in this state | 0 |
| Excluded companies in the tracked list or in proposals | BR-001.4 | JD.com tracked; Alibaba, JD.com, Baidu and Foxconn proposed | 0 |
| Proposed sources older than 30 days and still unresolved | BR-001.5 | 622 of 963 | 0 |
| Pages proposed more than once under a different address | BR-001.5 | Common (one company's page appears 4 times) | 0 new duplicates |

## 5. Constraints

- The daily run has a 60-minute limit and is already close to it. This work must not
  cause runs to fail on time.
- Spending per run should not rise as a result of this work.
- What the dashboard and public board show to users does not change, apart from the new
  feed-health indication.
- The project's working rules apply: every change through a pull request Henry reviews,
  tests written first, both frontend test suites at 80% or higher.

## 6. Assumptions to confirm in the spec stage

- The 19 Workday companies whose feed address looks correct are failing because of a
  fault in the product, not because the addresses are wrong. This has not been confirmed.
- The correct Workday addresses already collected for 14 other companies still work.

## 7. Out of scope

Each of these is a real need and would get its own business requirement.

- Run time against the 60-minute limit (candidate BR-002).
- Confirming that postings found by web-search discovery are still open; 235 have never
  been checked (candidate BR-003).
- Whether product marketing roles should be shown.

## 8. Traceability

Every acceptance criterion in the spec must name the requirement it serves. Current
mapping to the ten criteria in `issues/fix-feed-handoff.md`:

| Requirement | Spec acceptance criteria |
| --- | --- |
| BR-001.1 Feed coverage | 2, 3, 4 |
| BR-001.2 No silent failures | 6 |
| BR-001.3 Feeds stay working | 7 |
| BR-001.4 Exclusions honored | 5 |
| BR-001.5 Proposed sources prove themselves | 8, 9 |
| Process, not a business requirement | 1 (investigation answers), 10 (tests pass) |

A criterion with no requirement behind it is either scope creep or a sign that a
requirement is missing.

## 9. Approval

| | Name | Date |
| --- | --- | --- |
| Approved by | Henry McGee | 2026-11-23 |
