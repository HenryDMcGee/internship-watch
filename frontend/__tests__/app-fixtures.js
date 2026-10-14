// A small fake of the dashboard API for the app.js tests. `data` is mutable, so a test can
// change it and the next load sees the change.
const { jsonResponse, makeFetch, loadPage, flush } = require("./helpers");

function posting(over) {
  return Object.assign({
    id: 1, company_name: "Amazon", title: "PM Intern", url: "https://amazon.jobs/x", source_name: "Amazon.jobs",
    locations: ["Seattle, WA"], location_match: "match", first_seen: "2026-10-14", posted_date: "2026-10-10",
    status: "open", applied: 0, user_status: null, public_hidden: 0, visitor_reports: 0, note: null,
    verified_via: "feed", verified_at: "2026-10-14", status_note: null,
  }, over || {});
}

function budgetsBody() {
  return {
    settings: {
      billing_mode: "subscription", daily_token_budget: 1000, daily_search_budget: 5, weekly_token_budget: 5000,
      daily_usd_budget: 2, monthly_usd_budget: 20, search_limit_per_run: 3, search_limit_per_company: 1,
      browser_checks_per_run: 4, share_discovery: 25, share_scout: 25, share_classification: 25, share_improvement: 25,
      model_discovery: "sonnet", model_scout: "sonnet", model_classification: "haiku", model_improvement: "sonnet",
    },
    usage: { daily: { tokens: 10, searches: 1, usd: 0.5 }, weekly: { tokens: 100 } },
    resets: { daily: { reset_at: "2026-10-14T13:00:00Z" }, weekly: null },
    forecast: { weekly_tokens: 1234, monthly_usd: 3.5 },
    changes: [{ changed_at: "2026-10-13T10:00:00Z", field: "daily_search_budget", old_value: 4, new_value: 5 }],
    browser_run: {
      plan: "free",
      today: { minutes_used: 1, minutes_budget: 10, minutes_left: 9 },
      cloudflare_free: { daily_minutes: 10, minutes_used: 1, reset_at: "2026-10-15T05:00:00Z" },
      month: null,
      backup_today: { pages: 1, pages_cap: 5, reasons: ["blocked"] },
      series_7d: [{ date: "2026-10-13", minutes: 2 }, { date: "2026-10-14", minutes: 1 }],
      last_runs: [{ at: "2026-10-14T13:00:00Z", browser_run_pages: 3, browser_run_minutes: 1, backup_pages: 1, failures: 0 }],
    },
  };
}

function freshData() {
  return {
    today: [posting({ id: 1 })],
    earlier: [posting({ id: 2, company_name: "Google", title: "APM Intern", first_seen: "2026-10-01", locations: ["Tokyo, Japan"] })],
    archived: [],
    sources: [{ id: 1, name: "Amazon Jobs", url: "https://amazon.jobs", kind: "Company career sites", found_count: 2, covers: "Amazon", last_checked: "2026-10-14", notes: "ok" }],
    careersPages: [],
    feedback: [],
    proposals: [],
    analytics: null,
    budgets: budgetsBody(),
    publicSite: { enabled: false },
    locations: { countries: ["US"], include_remote: false },
    titles: { exclude_words: ["senior"], titles: [
      { title: "Product Manager Intern", group: "Product", aliases: ["PM Intern"], active: true },
      { title: "Associate Product Manager", group: "Product", aliases: [], active: false },
      { title: "Strategy Intern", group: "Strategy", aliases: [], active: true },
    ] },
    pendingTitles: { pending: null },
    companies: [],
    searchStatus: { busy: false },
    posts: [],
  };
}

function routes(data) {
  const rec = (key, result) => (url, opts) => {
    data.posts.push({ key, url, body: opts && opts.body ? JSON.parse(opts.body) : undefined });
    return typeof result === "function" ? result(url, opts) : jsonResponse(result);
  };
  const list = (url) => {
    const tab = /tab=(\w+)/.exec(url)[1];
    return jsonResponse({ postings: data[tab] });
  };
  return {
    "GET /api/postings": list,
    "GET /api/sources": () => jsonResponse({ sources: data.sources, careers_pages: data.careersPages }),
    "GET /api/feedback": () => jsonResponse({ feedback: data.feedback }),
    "GET /api/proposals": () => jsonResponse({ proposals: data.proposals }),
    "GET /api/analytics": () => jsonResponse(data.analytics),
    "GET /api/budgets": () => jsonResponse(data.budgets),
    "PUT /api/budgets": rec("put-budgets", () => jsonResponse(data.budgets)),
    "POST /api/budgets/resets": rec("reset", () => jsonResponse(data.budgets)),
    "GET /api/settings/public-site": () => jsonResponse(data.publicSite),
    "PUT /api/settings/public-site": rec("put-public", (u, o) => {
      data.publicSite = JSON.parse(o.body);
      return jsonResponse(data.publicSite);
    }),
    "GET /api/settings/locations": () => jsonResponse(data.locations),
    "PUT /api/settings/locations": rec("put-locations", () => jsonResponse({})),
    "GET /api/titles": () => jsonResponse(data.titles),
    "PUT /api/titles": rec("put-titles", () => jsonResponse({ pending: { html_url: "https://github.com/x/pull/9", number: 9, title: "Titles" } })),
    "GET /api/titles/pending": () => jsonResponse(data.pendingTitles),
    "GET /api/companies": () => jsonResponse({ companies: data.companies }),
    "GET /api/search/status": () => jsonResponse(data.searchStatus),
  };
}

// Boots the dashboard. `extra` adds or replaces routes: { "POST /api/x": body-or-function }.
async function bootApp(data, extra) {
  const fetchMock = makeFetch(Object.assign(routes(data), extra || {}));
  const mod = loadPage("index.html", "app.js", fetchMock);
  await flush();
  return { mod, fetchMock, data };
}

const $ = (id) => document.getElementById(id);
const click = (id) => $(id).click();

function type(id, value, eventName) {
  $(id).value = value;
  $(id).dispatchEvent(new Event(eventName || "input", { bubbles: true }));
}

function buttonByText(root, text) {
  return Array.from(root.querySelectorAll("button")).find((b) => b.textContent === text);
}

module.exports = { jsonResponse, posting, freshData, bootApp, flush, $, click, type, buttonByText, budgetsBody };
