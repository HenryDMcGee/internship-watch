const { jsonResponse, makeFetch, loadPage, flush } = require("./helpers");

const TODAY = "2026-10-14";

function posting(over) {
  return Object.assign({
    id: 1, company_name: "Amazon", title: "PM Intern", title_en: "", locations: ["Seattle, WA"],
    posted_date: "2026-10-01", first_seen: TODAY, url: "https://amazon.jobs/x",
  }, over || {});
}

function boot(body, extra) {
  const routes = Object.assign({ "GET /api/public/postings": body }, extra || {});
  const fetchMock = makeFetch(routes);
  const mod = loadPage("public/index.html", "public/public.js", fetchMock);
  return { mod, fetchMock };
}

beforeEach(() => {
  jest.useFakeTimers({ doNotFake: ["setTimeout", "nextTick", "setImmediate", "queueMicrotask"] });
  jest.setSystemTime(new Date("2026-10-14T18:00:00Z"));
});
afterEach(() => jest.useRealTimers());

describe("pure helpers", () => {
  let mod;
  beforeEach(() => { mod = boot({ enabled: false }).mod; });

  test("splitByTab puts today's finds first and everything else under earlier", () => {
    const rows = [posting({ id: 1 }), posting({ id: 2, first_seen: "2026-10-01" })];
    const out = mod.splitByTab(rows, TODAY);
    expect(out.today.map((p) => p.id)).toEqual([1]);
    expect(out.earlier.map((p) => p.id)).toEqual([2]);
  });

  test("matches searches company, title, english title and locations", () => {
    const p = posting({ title: "プロダクト", title_en: "Product Intern", locations: ["Tokyo, Japan"] });
    expect(mod.matches(p, "")).toBe(true);
    expect(mod.matches(p, "amazon")).toBe(true);
    expect(mod.matches(p, "product intern")).toBe(true);
    expect(mod.matches(p, "tokyo")).toBe(true);
    expect(mod.matches(p, "zzz")).toBe(false);
    expect(mod.matches({ company_name: "X", title: "Y" }, "x")).toBe(true);
  });

  test("titleOf prefers the english title", () => {
    expect(mod.titleOf({ title: "a", title_en: "b" })).toBe("b");
    expect(mod.titleOf({ title: "a" })).toBe("a");
    expect(mod.titleOf({})).toBe("");
  });

  test("safeUrl only allows http and https", () => {
    expect(mod.safeUrl("https://a.com/x")).toBe("https://a.com/x");
    expect(mod.safeUrl("javascript:alert(1)")).toBeNull();
    expect(mod.safeUrl("http://[bad")).toBeNull();
  });

  test("fmtDate and fmtDateTime", () => {
    expect(mod.fmtDate("")).toBe("");
    expect(mod.fmtDate("nope")).toBe("nope");
    expect(mod.fmtDate("2026-10-14")).toMatch(/Oct/);
    expect(mod.fmtDateTime("")).toBe("");
    expect(mod.fmtDateTime("garbage")).toBe("");
    expect(mod.fmtDateTime("2026-10-14T18:30:00Z")).toMatch(/Oct 14, 1:30.*PM Central/);
  });

  test("centralToday uses Central time", () => {
    jest.setSystemTime(new Date("2026-10-15T03:00:00Z"));
    expect(mod.centralToday()).toBe("2026-10-14");
  });

  test("feedbackError rejects blank messages", () => {
    expect(mod.feedbackError("   ")).toBe("Write a message first.");
    expect(mod.feedbackError(" hi ")).toBeNull();
  });

  test("feedbackPayload carries every field", () => {
    expect(mod.feedbackPayload({ kind: "closed", message: "m", postingId: 4, pageUrl: "u", website: "w" })).toEqual({
      kind: "closed", message: "m", posting_id: 4, page_url: "u", website: "w",
    });
  });

  test("feedbackStatusText maps responses to the visitor's message", () => {
    expect(mod.feedbackStatusText({ status: 429, ok: false })).toBe("Try again tomorrow.");
    expect(mod.feedbackStatusText({ status: 204, ok: true })).toBe("Thanks, sent.");
    expect(mod.feedbackStatusText({ status: 400, ok: false })).toMatch(/Couldn't send that\. Please check/);
  });
});

describe("page", () => {
  test("site off shows only the unavailable line", async () => {
    boot({ enabled: false });
    await flush();
    expect(document.getElementById("unavailable").hidden).toBe(false);
    expect(document.getElementById("board").hidden).toBe(true);
  });

  test("a failed load shows the unavailable line", async () => {
    boot({}, { "GET /api/public/postings": () => jsonResponse({}, 500) });
    await flush();
    expect(document.getElementById("unavailable").hidden).toBe(false);
  });

  test("renders cards, tabs and search", async () => {
    boot({
      enabled: true, last_run: "2026-10-14T13:00:00Z",
      postings: [
        posting({ id: 1 }),
        posting({ id: 2, company_name: "Google", title: "APM Intern", first_seen: "2026-10-01", url: "javascript:x", posted_date: null, locations: [] }),
      ],
    });
    await flush();
    expect(document.getElementById("board").hidden).toBe(false);
    expect(document.getElementById("lastUpdated").textContent).toMatch(/Last updated .* Central\./);
    expect(document.getElementById("nToday").textContent).toBe("1");
    expect(document.querySelectorAll("#list li.item")).toHaveLength(1);
    expect(document.querySelector("#list a").href).toBe("https://amazon.jobs/x");

    document.getElementById("tabEarlier").click();
    expect(document.getElementById("tabEarlier").getAttribute("aria-selected")).toBe("true");
    expect(document.querySelector("#list h3").textContent).toBe("Google");
    expect(document.querySelector("#list a")).toBeNull();

    const filter = document.getElementById("filter");
    filter.value = "zzz";
    filter.dispatchEvent(new Event("input"));
    expect(document.querySelector("#list .empty").textContent).toBe("No postings match that search");

    filter.value = "";
    filter.dispatchEvent(new Event("input"));
    document.getElementById("tabToday").click();
    expect(document.querySelectorAll("#list li.item")).toHaveLength(1);
  });

  test("empty tabs say so", async () => {
    boot({ enabled: true, postings: [] });
    await flush();
    expect(document.querySelector("#list .empty").textContent).toBe("Nothing new yet today");
    document.getElementById("tabEarlier").click();
    expect(document.querySelector("#list .empty").textContent).toBe("No earlier postings");
    expect(document.getElementById("lastUpdated").textContent).toBe("");
  });

  test("report opens the dialog for that posting and sends feedback", async () => {
    const { fetchMock } = boot({ enabled: true, postings: [posting({ id: 7 })] },
      { "POST /api/public/feedback": () => ({ ok: true, status: 204, json: async () => null }) });
    await flush();
    const dialog = document.getElementById("feedbackDialog");
    dialog.showModal = jest.fn();
    dialog.close = jest.fn();
    document.querySelector("#list button").click();
    expect(dialog.showModal).toHaveBeenCalled();
    expect(document.getElementById("fbPosting").textContent).toBe("About: Amazon, PM Intern");
    expect(document.getElementById("fbKind").value).toBe("dead_link");

    document.getElementById("fbSend").click();
    expect(document.getElementById("fbMsg").textContent).toBe("Write a message first.");

    document.getElementById("fbMessage").value = "dead";
    document.getElementById("fbSend").click();
    await flush();
    expect(document.getElementById("fbMsg").textContent).toBe("Thanks, sent.");
    const sent = fetchMock.calls.find((c) => c.method === "POST");
    expect(sent.body.posting_id).toBe(7);
    expect(document.getElementById("fbSend").disabled).toBe(false);

    document.getElementById("fbClose").click();
    expect(dialog.close).toHaveBeenCalled();
  });

  test("429, server error and network error messages", async () => {
    let mode = 429;
    boot({ enabled: true, postings: [] }, {
      "POST /api/public/feedback": () => {
        if (mode === "net") throw new Error("offline");
        return { ok: false, status: mode, json: async () => ({}) };
      },
    });
    await flush();
    document.getElementById("openFeedback").click(); // no showModal in jsdom: falls back to the open attribute
    expect(document.getElementById("feedbackDialog").hasAttribute("open")).toBe(true);
    expect(document.getElementById("fbPosting").hidden).toBe(true);
    expect(document.getElementById("fbKind").value).toBe("suggestion");
    document.getElementById("fbMessage").value = "hello";
    const cases = [[429, "Try again tomorrow."], [500, /Couldn't send that\. Please check/], ["net", "Couldn't send that. Please try again."]];
    for (const [m, text] of cases) {
      mode = m;
      document.getElementById("fbSend").click();
      await flush();
      const got = document.getElementById("fbMsg").textContent;
      if (text instanceof RegExp) expect(got).toMatch(text); else expect(got).toBe(text);
    }
    document.getElementById("fbClose").click();
    expect(document.getElementById("feedbackDialog").hasAttribute("open")).toBe(false);
  });
});
