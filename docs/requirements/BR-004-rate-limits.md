# BR-004 — A site that asks us to slow down is still read, and never costs us a source

| | |
| --- | --- |
| Status | **Approved by Henry, 2026-11-24** ("make a br and pr for the fix") |
| Owner | Henry McGee |
| Written | 2026-11-24 |
| Evidence | D1 queries against `internship-watch-db` during run 111, 2026-11-24 |
| Spec that implements it | `issues/workday-rate-limit.md` |
| Follows from | BR-001 (`docs/requirements/BR-001-feed-coverage.md`) |

BR-002 and BR-003 are kept for the two candidates BR-001 names (run time, and checking
that postings found by web search are still open), so this one takes the next number.

A business requirement says what the product must achieve and how we will know. It names
no files, tables or code.

---

## 1. The business need

BR-001 got the Workday reader working. The first two runs after it shipped showed the
next obstacle: Workday answers most of our reads with "too many requests".

- 19 tracked companies have a correct Workday address. On run 111, **2 were read**
  (Fiserv, 74 postings; Microchip, 71) and **17 were refused** with HTTP 429, including
  NVIDIA, PayPal, HP, Snap and Autodesk. On run 110 all 19 were refused.
- A refused read counts as a failed read. Nothing in the product slows down, waits, or
  tries again, so the same thing will happen on every run.
- **A refusal can cost us a good source for good.** A proposed feed address is tried
  once. If that one try is refused, the address is demoted and never proposed again,
  although nothing is wrong with it. 18 companies have addresses waiting for that try.

The cost is the one BR-001 set out to remove: postings at NVIDIA, Salesforce, Qualcomm,
Micron and the rest are still not read through their feeds, and a busy morning at
Workday could now throw away the addresses we need to fix that.

## 2. Who this is for

| Stakeholder | What they need from this |
| --- | --- |
| Henry, as the primary user | Workday companies read through their feeds, as BR-001 promised |
| Henry, as the owner | Not to lose a good source because a site was busy; to see when a site keeps refusing us |
| The companies whose sites we read | A reader that slows down when asked and stops when refused |

## 3. Requirements

**BR-004.1 — A site that asks us to slow down is still read.**
When a job site says we are asking too fast, the product slows down and reads the
company's feed in the same run.

**BR-004.2 — A temporary refusal never costs us a source.**
A proposed source or a working feed is dropped or replaced only on evidence that its
address is wrong. Being told to slow down, a timeout, or a site that is briefly down is
not that evidence.

**BR-004.3 — We do what the site asks.**
The product waits as long as a site asks, tries again once, and stops asking a site that
keeps refusing. It never tries to get around a refusal.

**BR-004.4 — A refusal that does not clear is visible.**
When a site keeps refusing us across runs, the owner can see which feeds are affected and
why, without querying the database.

## 4. How we will know

Baselines were measured on run 111, 2026-11-24.

| Measure | Requirement | Today | Target |
| --- | --- | --- | --- |
| Workday companies with an address that are read per run | BR-004.1 | 2 of 19 | 17 of 19 or more |
| Sources demoted because of a temporary refusal | BR-004.2 | None yet; 18 addresses at risk on the next run | 0, ever |
| Feeds replaced because of a temporary refusal | BR-004.2 | None yet; 17 at risk after 3 more runs | 0, ever |
| Requests sent to a site after it has refused us three reads in a row in a run | BR-004.3 | No limit | 0 |
| Refused feeds named to the owner with the reason | BR-004.4 | Named, reason not shown | Named, with the reason |

## 5. Constraints

- The daily run has a 60-minute limit and run 111 reached it. Time spent waiting on a
  site must have a ceiling per run, and the ceiling must be small.
- The project rule stands: respect sites that block automated readers, and never try to
  get around a block.
- Spending per run should not rise.

## 6. Assumptions to confirm

- **Why Workday refuses us is not confirmed.** The evidence fits a limit on how fast one
  reader may ask, which slowing down would satisfy. It also fits Workday refusing the
  addresses GitHub's runners use, which slowing down would not. The first run after this
  ships will tell, because the refusal will be recorded with the request it happened on.
- If Workday refuses GitHub's runners outright, BR-004.1 cannot be met from there and
  the answer is a decision for Henry, not a workaround.

## 7. Out of scope

- Run time against the 60-minute limit (candidate BR-002). This work adds a small,
  capped amount of waiting and must not be the thing that fixes or breaks it.
- Reading Workday from somewhere other than GitHub's runners.

## 8. Traceability

| Requirement | Spec acceptance criteria |
| --- | --- |
| BR-004.1 A site that asks us to slow down is still read | 1, 2, 3 |
| BR-004.2 A temporary refusal never costs us a source | 4, 5, 6 |
| BR-004.3 We do what the site asks | 7, 8 |
| BR-004.4 A refusal that does not clear is visible | 9 |
| Process, not a business requirement | 10 (tests pass) |

## 9. Approval

| | Name | Date |
| --- | --- | --- |
| Approved by | Henry McGee | 2026-11-24 |
