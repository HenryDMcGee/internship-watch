"""Test server for the Playwright suite (issue #81).

Serves the real FastAPI app (api/src/main.py) on top of the in-memory SqliteDatabase, with
the schema built the way tests/conftest.py builds it, and serves frontend/ the way the
`ASSETS` binding does. Two ports stand in for the two hostnames:

  dashboard  127.0.0.1:8787  -> internship-watch.mcgeehenry.com (everything)
  public     127.0.0.1:8788  -> jobs.mcgeehenry.com (only what route_for_host allows;
                                `/` shows frontend/public/index.html)

The harness uses main.py's own `route_for_host` and `rewrite_public_path`, so the routing
rules under test are the real ones. It does not run the Pyodide entrypoint (`Default.fetch`).
Cloudflare Access stays unconfigured, as it is locally. GitHub is a small in-memory fake.

Test-only controls live on the dashboard port under /__test/ (never on the public port):
  POST /__test/reset            {"public_site": bool, "rate_limited": bool, "unconfirmed": bool}  re-seeds the database
  POST /__test/complete-search  {"result": {...}, "status": "completed"|"failed"}  finishes the pending "Search now" request
  POST /__test/complete-check   {"result": {...}, "status": "completed"|"failed"}  finishes the pending "Check now" request
"""
from __future__ import annotations

import base64
import json
import mimetypes
import sys
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
for entry in (ROOT, ROOT / "api", ROOT / "api" / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from fastapi.testclient import TestClient  # noqa: E402

from github_client import GitHubClient  # noqa: E402  (api/src is on the path, as in tests/conftest.py)
from src.db import SqliteDatabase  # noqa: E402
from src.main import app, central_today, rewrite_public_path, route_for_host, visitor_hash  # noqa: E402
from tests.conftest import fresh_schema_connection  # noqa: E402

FRONTEND = ROOT / "frontend"
PRIVATE_HOST = "internship-watch.mcgeehenry.com"
PUBLIC_HOST = "jobs.mcgeehenry.com"
SALT = "ui-test-salt"
VISITOR_IP = "127.0.0.1"
LOCK = threading.Lock()
client = TestClient(app)


# -- seeded data ---------------------------------------------------------------------

def _insert(conn, table: str, row: dict) -> int:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    return conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values())).lastrowid


def seed(public_site: bool = False, rate_limited: bool = False, unconfirmed: bool = False) -> SqliteDatabase:
    conn = fresh_schema_connection()
    today = central_today()
    earlier = (date.fromisoformat(today) - timedelta(days=5)).isoformat()

    def posting(**over):
        row = {
            "company_name": "Acme", "title": "Product Intern", "url": "https://example.com/job", "source_name": "Acme feed",
            "locations": json.dumps(["Seattle, WA"]), "location_match": "match", "first_seen": today, "status": "open",
            "posted_date": today, "verified_via": "feed", "verified_at": today,
        }
        row.update(over)
        return _insert(conn, "postings", row)

    # Public: ids 1-4. Everything after is private or filtered out of the public page.
    posting(company_name="Amazon", title="Product Manager Intern", url="https://amazon.jobs/1",
            note="PRIVATE-NOTE-TEXT", applied=1, user_status="open", status_note="PRIVATE-STATUS-NOTE")
    posting(company_name="Google", title="APM Intern", locations=json.dumps(["Mountain View, CA", "New York, NY"]),
            url="https://google.example/apm")
    posting(company_name="Microsoft", title="Program Manager Intern", first_seen=earlier,
            locations=json.dumps(["Redmond, WA"]), url="https://microsoft.example/pm")
    posting(company_name="Sony", title="プロダクトインターン", title_en="Product Intern", first_seen=earlier,
            locations=json.dumps(["Tokyo, Japan"]), url="https://sony.example/product")
    posting(company_name="Meta", title="Closed Role", status="closed", status_note="Page is gone")
    posting(company_name="Spotify", title="Outside Role", location_match="outside", locations=json.dumps(["Stockholm"]))
    posting(company_name="Netflix", title="Hidden Role", public_hidden=1)
    posting(company_name="Oracle", title="Archived Role", archived_at=today, archive_reason="deleted")
    posting(company_name="Unclear Co", title="Unclear Role", location_match="unclear", status="unverified",
            first_seen=earlier, url="javascript:alert(1)")
    if unconfirmed:
        # Found today on a careers page or by web search, status not confirmed yet: shown publicly with a tag.
        posting(company_name="Nintendo", title="Product Strategy Intern", status="unverified", verified_via=None,
                verified_at=None, locations=json.dumps(["Redmond, WA"]), url="https://nintendo.example/ps")

    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('public_site_enabled', ?)",
                 ("true" if public_site else "false",))

    _insert(conn, "sources", {"name": "Amazon Jobs", "url": "https://amazon.jobs", "kind": "Company career sites",
                              "covers": "Amazon", "found_count": 3, "last_checked": today, "notes": "Works well"})
    _insert(conn, "sources", {"name": "Example Board", "url": "https://board.example", "kind": "Job boards", "found_count": 1})
    _insert(conn, "sources", {"name": "Elsewhere", "url": "https://elsewhere.example", "kind": "Surprise kind"})

    _insert(conn, "companies", {"name": "Zed Corp", "status": "review", "careers_url": "https://zed.example/careers"})
    _insert(conn, "companies", {"name": "Apple", "status": "ranked", "rank": 1, "feed_type": "greenhouse",
                                "careers_url": "https://boards.greenhouse.io/apple", "last_feed_status": "ok"})
    _insert(conn, "companies", {"name": "Borderline", "status": "pinned", "last_feed_status": "no_source"})
    _insert(conn, "companies", {"name": "Brokenfeed", "status": "pinned", "careers_url": "https://b.example",
                                "feed_type": "lever", "last_feed_status": "error", "last_feed_error": "HTTP 500"})
    _insert(conn, "companies", {"name": "Careerco", "status": "pinned", "careers_url": "https://careerco.example/jobs",
                                "careers_page_flagged": 1})
    _insert(conn, "companies", {"name": "Gone Inc", "status": "removed"})
    careerco = conn.execute("SELECT id FROM companies WHERE name = 'Careerco'").fetchone()[0]
    _insert(conn, "careers_page_snapshots", {"company_id": careerco, "last_checked": today + "T13:00:00Z", "status": "failed",
                                             "method": "browser_run", "consecutive_failures": 3, "last_error": "timeout"})

    run_id = _insert(conn, "runs", {"kind": "daily", "started_at": today + "T13:00:00Z", "finished_at": today + "T13:05:00Z",
                                    "status": "completed", "new_count": 2, "summary": "Found 2."})
    _insert(conn, "runs", {"kind": "daily", "started_at": earlier + "T13:00:00Z", "finished_at": earlier + "T14:10:00Z",
                           "status": "degraded", "new_count": 0, "summary": "x. Run degraded because a feed failed. No new postings"})
    _insert(conn, "usage", {"run_id": run_id, "step": "discovery", "model": "sonnet", "input_tokens": 1000,
                            "output_tokens": 500, "web_searches": 2, "est_cost_usd": 0.12, "at": today + "T13:01:00Z"})
    _insert(conn, "alerts", {"type": "feed_down", "severity": "warning", "message": "A feed is down",
                             "created_at": today + "T13:02:00Z"})
    _insert(conn, "browser_run_usage", {"run_id": run_id, "at": today + "T13:03:00Z", "method": "browser_run", "pages": 4,
                                        "unchanged": 2, "changed": 1, "browser_ms": 60000, "measured_by": "elapsed", "failures": 1})
    _insert(conn, "browser_run_usage", {"run_id": run_id, "at": today + "T13:04:00Z", "method": "backup", "pages": 2,
                                        "failures": 0, "backup_reason": "blocked"})

    _insert(conn, "proposals", {"kind": "source", "payload": json.dumps({"name": "New Board", "url": "https://new.example",
                                                                         "reason": "Looks useful"}),
                                "created_at": today + "T12:00:00Z"})
    _insert(conn, "proposals", {"kind": "company", "payload": json.dumps({"action": "add", "name": "Newco"}),
                                "created_at": today + "T12:01:00Z"})

    stamp = today + "T14:00:00Z"
    _insert(conn, "feedback", {"source": "public", "kind": "dead_link", "posting_id": 2, "message": "Seeded visitor note <b>x</b>",
                               "page_url": "https://jobs.mcgeehenry.com/", "visitor_hash": "someone-else", "created_at": stamp})
    _insert(conn, "feedback", {"source": "public", "kind": "suggestion", "message": "Second note",
                               "visitor_hash": "someone-else", "created_at": stamp})
    if rate_limited:
        mine = visitor_hash(SALT, VISITOR_IP)
        for i in range(10):
            _insert(conn, "feedback", {"source": "public", "kind": "other", "message": f"earlier {i}", "visitor_hash": mine,
                                       "status": "dismissed", "created_at": stamp})
    conn.commit()
    return SqliteDatabase(conn)


# -- a small fake of the GitHub API (titles pull request, issues, workflow dispatch) ----

class FakeGitHub:
    def __init__(self):
        self.titles = (ROOT / "config" / "titles.json").read_text(encoding="utf-8")
        self.pr = None

    async def __call__(self, method, url, *, token, json_body=None):
        path = url.split("api.github.com", 1)[1]
        if method == "GET" and "/pulls?" in path:
            return 200, [self.pr] if self.pr else []
        if method == "GET" and "/contents/config/titles.json" in path:
            return 200, {"content": base64.b64encode(self.titles.encode("utf-8")).decode("ascii"), "sha": "sha1"}
        if method == "GET" and "/git/ref/heads/" in path:
            return 200, {"object": {"sha": "abc123"}}
        if method == "PUT" and "/contents/config/titles.json" in path:
            self.titles = base64.b64decode(json_body["content"]).decode("utf-8")
            return 200, {}
        if method == "POST" and path.endswith("/pulls"):
            self.pr = {"number": 12, "html_url": "https://github.com/HenryDMcGee/internship-watch/pull/12",
                       "title": json_body["title"]}
            return 201, self.pr
        if method == "POST" and path.endswith("/issues"):
            return 201, {"html_url": "https://github.com/HenryDMcGee/internship-watch/issues/77"}
        if method == "POST" and "/dispatches" in path:
            return 204, {}
        return 404, {"message": "not in the fake: " + method + " " + path}


def reset(public_site: bool = False, rate_limited: bool = False, unconfirmed: bool = False) -> None:
    app.state.db = seed(public_site, rate_limited, unconfirmed)
    app.state.github = GitHubClient(repo="HenryDMcGee/internship-watch", token="test", transport=FakeGitHub())
    app.state.access_team_domain = ""
    app.state.access_aud = ""
    app.state.feedback_salt = SALT


def _complete(table: str, result: dict, status: str = "completed") -> None:
    db = app.state.db
    db._conn.execute(f"UPDATE {table} SET status = ?, result = ? WHERE status IN ('pending', 'running')",
                     (status, json.dumps(result)))
    db._conn.commit()


# -- HTTP ----------------------------------------------------------------------------

def make_handler(hostname: str, controls: bool):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, status: int, body: bytes = b"", content_type: str = "text/plain; charset=utf-8", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _handle(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            path = self.path.split("?", 1)[0]

            if path.startswith("/__test/"):
                if not controls:
                    return self._send(404, b"Not found")
                payload = json.loads(raw or b"{}")
                with LOCK:
                    if path == "/__test/reset":
                        reset(bool(payload.get("public_site")), bool(payload.get("rate_limited")),
                              bool(payload.get("unconfirmed")))
                    elif path == "/__test/complete-search":
                        _complete("search_requests", payload.get("result") or {}, payload.get("status", "completed"))
                    elif path == "/__test/complete-check":
                        _complete("check_requests", payload.get("result") or {}, payload.get("status", "completed"))
                    else:
                        return self._send(404, b"Not found")
                return self._send(204)

            route = route_for_host(hostname, path, PUBLIC_HOST)
            if route == "not_found":
                return self._send(404, b"Not found")

            if path.startswith("/api/"):
                headers = {"CF-Connecting-IP": VISITOR_IP}
                if raw:
                    headers["Content-Type"] = self.headers.get("Content-Type", "application/json")
                with LOCK:
                    resp = client.request(self.command, self.path, content=raw or None, headers=headers)
                extra = {"Cache-Control": resp.headers["cache-control"]} if "cache-control" in resp.headers else {}
                return self._send(resp.status_code, resp.content, resp.headers.get("content-type", "text/plain"), extra)

            if route == "public":
                path = rewrite_public_path(path)
            if path.endswith("/"):
                path += "index.html"
            target = (FRONTEND / path.lstrip("/")).resolve()
            if FRONTEND not in target.parents or not target.is_file():
                return self._send(404, b"Not found")
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return self._send(200, target.read_bytes(), kind + ("; charset=utf-8" if kind.startswith("text/") or "javascript" in kind else ""))

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _handle

    return Handler


def main() -> None:
    dashboard_port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    public_port = int(sys.argv[2]) if len(sys.argv) > 2 else 8788
    reset()
    servers = [
        ThreadingHTTPServer(("127.0.0.1", dashboard_port), make_handler(PRIVATE_HOST, controls=True)),
        ThreadingHTTPServer(("127.0.0.1", public_port), make_handler(PUBLIC_HOST, controls=False)),
    ]
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
