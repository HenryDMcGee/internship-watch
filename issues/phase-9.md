Phase 9: Budgets dialog redesign, dollars-only billing

---

Henry approved this redesign on October 19, 2026 (the "Budgets Dialog Redesign" canvas: a default-state artboard and a validation-state artboard, dark theme). Build this phase directly. If you need to go beyond this scope, comment with a proposal in three sections (Functionality, Technical code changes, Architecture) and wait for approval.

Read `CLAUDE.md`, `docs/plan.md`, and `docs/deploy.md` first. This phase assumes Phase 7 (public job board) is merged. Write the tests for each change before the implementation (TDD).

## Goal

One billing model, API-key dollars, and a streamlined Budgets dialog: only dollar and search limits visible, whole-percent step shares with a live total, inline validation before anything is sent, rarely-changed limits collapsed, and the Public site and Browser Run status inside the dialog. The subscription/token billing mode is removed everywhere.

## Build

### Settings and API

- Settings doc: remove `billing_mode`, `daily_token_budget`, and `weekly_token_budget`. Keep `daily_usd_budget`, `monthly_usd_budget`, `daily_search_budget`, `search_limit_per_run`, `search_limit_per_company`, `browser_checks_per_run`, and the four `share_*` (still 0 to 1 decimals summing to 1.0) and `model_*` fields. Add `public_feedback_daily_limit` (integer, default 20). One-time migration of the stored settings doc: strip the removed keys, keep every other value, and record the migration in the budgets change history.
- `api/src/budgets.py`: update `NUMERIC_FIELDS` and `validate_budget_settings` for the new shape (no `billing_mode` branch; `public_feedback_daily_limit` validated as a non-negative integer). `daily_fraction_used` becomes the worse of the dollar fraction and the search fraction. Mirror the same changes in `job/agent/budgets.py` (the two stay separate, per the module docstrings).
- `GET /api/budgets`: usage adds this month's dollars (for "Today: $x of $y … This month: $a of $b"); forecast returns weekly and monthly dollars; the payload adds the Public site block (feedback cap, today's feedback count, board views) and drops all token fields.
- Daily job (`job/agent/budgets.py`): enforcement is dollars and searches only. Weekly token enforcement is removed; weekly dollars are forecast but not enforced. The "Search now" 80%-used confirmation (issue #35) uses the dollar fraction.
- Public feedback cap: the `/api/public` feedback endpoint from Phase 7 enforces `public_feedback_daily_limit` (in addition to the honeypot). If that endpoint is not merged when this phase starts, add the setting, validation, and dashboard display now and leave one clearly marked enforcement hook for Phase 7.

### Budgets dialog (`frontend/index.html`, `frontend/app.js`, `frontend/style.css`)

Match the approved canvas. The mockup is drawn in the dark theme; implement with the existing CSS variables so both themes work.

- Remove the Billing section and the mode select. Limits shows exactly three inputs: Daily $, Monthly $, Daily searches.
- Step shares: whole percents (0 to 100) in the UI, converted to 0 to 1 decimals in `readBudgetsForm`/`fillBudgetsForm` so the API contract is unchanged apart from the removed fields. One row per step: step name, percent input, model select. Below the rows, a live total pill: green "Total: 100% (checkmark)" when complete, dashed red "Total: n% — m% left to assign" otherwise, plus an "Even split" button that sets 25/25/25/25.
- Inline validation: every numeric input flags non-negative/numeric problems next to the field as the user types. Save is disabled with the message "Fix the flagged fields to save." while the total is not 100% or any field is invalid. Server validation stays as the backstop.
- Advanced limits: searches per run, searches per company, and browser checks per run move into a collapsed `<details>` section.
- Forecast: "This week: $x · This month: $y".
- Usage and resets: today in dollars, this month in dollars, searches used; the "Reset today's usage" and "Reset this week's usage" buttons stay as they are.
- Public site section (new): the feedback cap input and a status line "Today: x of y used · n board views".
- Browser Run: keep the summary lines, the runs table, and the Cloudflare footnote. Remove the 7-day bar chart and its render code (`renderBrowserRunChart`, the `.br-chart` styles).
- Analytics view: the "Usage against budget" summary switches to dollars and loses the billing-mode wording.

### Tests first

- pytest: `validate_budget_settings` accepts the new doc shape and reports each bad field; shares must still sum to 1.0; `daily_fraction_used` uses dollars; the migration strips the removed keys and preserves the rest; the budgets payload contains monthly dollars and the Public site block and no token fields; the feedback cap rejects negatives.
- Jest: percent-to-decimal round trip; total pill states at 90%, 100%, and over 100%; Save gating for an incomplete total and for an invalid field; the dialog renders with the Billing section and chart gone. Both existing 80% coverage gates stay.

### Docs

Update `docs/plan.md` with this phase. No new routes or wrangler vars are expected; note in `docs/deploy.md` only if that changes.

## Done when

- The dialog matches the approved canvas in both themes: no billing mode, no token inputs, percent shares with the live total, Advanced collapsed, Public site and Browser Run (table only) present.
- Shares cannot be saved unless they total 100%, and invalid values are flagged before any request is made.
- The daily job enforces dollar and search budgets only, and existing settings survive the migration with their dollar values intact.
- The feedback cap is stored, shown in the dialog, and enforced by the public feedback endpoint.
- CI passes with both coverage gates.
