"""A test WordPress: the part of the core REST API the publishing flow uses, in memory. Used by the tests only.

It behaves like the real one where the flows depend on it: a post made without a status is a draft, saving a post with a changed
content keeps the old version as a revision, DELETE without force moves a post to the trash, and what was asked of it is
recorded (`requests`) so a test can check that nothing ever asked for a status of publish."""

import json
import re
import urllib.parse
from datetime import datetime, timedelta

ROUTE = re.compile(r"^/wp/v2/(posts|media|categories)(?:/(\d+))?(?:/(revisions))?$")


class FakeWordPress:

    def __init__(self, base="https://example.test", user="pulseo-editor", password="secretpassword", now=None):
        self.base, self.user, self.password = base, user, password
        self.clock = now or datetime(2026, 11, 14, 9, 0, 0)
        self.posts, self.media, self.revisions, self.requests = {}, {}, {}, []
        self.next_id = 100
        self.categories = [{"id": 1, "name": "Uncategorized"}, {"id": 7, "name": "Guides"}]

    # ---- the world the flows look at ------------------------------------------------------------------------------
    def tick(self, minutes=1) -> str:
        self.clock += timedelta(minutes=minutes)
        return self.clock.isoformat()

    def add_post(self, title, content, status="publish", slug="", categories=None, featured=0, excerpt="", published=None) -> int:
        self.next_id += 1
        pid = self.next_id
        stamp = self.tick()
        self.posts[pid] = {"id": pid, "status": status, "slug": slug or f"post-{pid}", "date": published or stamp,
                           "modified": stamp, "author": 3, "featured_media": featured, "categories": categories or [1],
                           "link": f"{self.base}/{slug or f'post-{pid}'}/", "title": {"raw": title, "rendered": title},
                           "content": {"raw": content, "rendered": content}, "excerpt": {"raw": excerpt, "rendered": excerpt},
                           "comment_status": "open"}
        self.revisions[pid] = []
        return pid

    def edit_elsewhere(self, pid, content):
        """Somebody edits a post in the WordPress editor."""
        self._save(pid, {"content": content})

    def _save(self, pid, fields):
        post = self.posts[pid]
        if "content" in fields and fields["content"] != post["content"]["raw"]:
            self.revisions[pid].append({"id": pid * 10 + len(self.revisions[pid]), "content": post["content"]["raw"]})
        for key in ("title", "content", "excerpt"):
            if key in fields:
                post[key] = {"raw": fields[key], "rendered": fields[key]}
        post["modified"] = self.tick()

    # ---- the request function the client calls --------------------------------------------------------------------
    def __call__(self, method, url, headers, body=None):
        parts = urllib.parse.urlsplit(url)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        own = urllib.parse.urlsplit(self.base)                              # an install can live in a folder (/fa)
        path = parts.path[len(own.path.rstrip("/")):] if parts.path.startswith(own.path.rstrip("/")) else parts.path
        route = path[len("/wp-json"):] if path.startswith("/wp-json") else query.pop("rest_route", "")
        payload = {}
        if body and headers.get("Content-Type", "").startswith("application/json"):
            payload = json.loads(body.decode())
        self.requests.append({"method": method, "route": route, "payload": payload, "query": query, "headers": dict(headers),
                              "size": len(body or b"")})
        if not parts.netloc or f"{parts.scheme}://{parts.netloc}" != f"{own.scheme}://{own.netloc}":
            return 0, {}, b""
        if not headers.get("Authorization"):
            return 401, {}, self._j({"code": "rest_forbidden"})
        m = ROUTE.match(route)
        if not m:
            return 404, {}, self._j({"code": "rest_no_route"})
        kind, pid, sub = m.group(1), int(m.group(2)) if m.group(2) else 0, m.group(3)
        return getattr(self, f"_{kind}")(method, pid, sub, payload, query, headers, body)

    @staticmethod
    def _j(data):
        return json.dumps(data).encode()

    def _posts(self, method, pid, sub, payload, query, headers, body):
        if sub == "revisions":
            if pid not in self.posts:
                return 404, {}, self._j({"code": "rest_post_invalid_id"})
            revs = self.revisions[pid]
            return 200, {"x-wp-total": str(len(revs))}, self._j([{"id": r["id"]} for r in revs])
        if method == "GET" and not pid:
            posts = list(self.posts.values())
            if query.get("status", "any") not in ("any", ""):
                posts = [p for p in posts if p["status"] == query["status"]]
            if query.get("slug"):
                posts = [p for p in posts if p["slug"] == query["slug"]]
            if query.get("after"):
                posts = [p for p in posts if p["date"] > query["after"]]
            page, per = int(query.get("page", 1)), int(query.get("per_page", 10))
            if (page - 1) * per >= max(len(posts), 1) and page > 1:
                return 400, {}, self._j({"code": "rest_post_invalid_page_number"})
            return 200, {"x-wp-total": str(len(posts))}, self._j(posts[(page - 1) * per:page * per])
        if method == "GET":
            return (200, {}, self._j(self.posts[pid])) if pid in self.posts else (404, {}, self._j({"code": "rest_post_invalid_id"}))
        if method == "POST" and not pid:
            new = self.add_post(payload.get("title", ""), payload.get("content", ""), payload.get("status", "draft"),
                                payload.get("slug", ""), payload.get("categories"), excerpt=payload.get("excerpt", ""))
            return 201, {}, self._j(self.posts[new])
        if pid not in self.posts:
            return 404, {}, self._j({"code": "rest_post_invalid_id"})
        if method == "POST":
            if "status" in payload:
                self.posts[pid]["status"] = payload["status"]
            self._save(pid, {k: v for k, v in payload.items() if k in ("title", "content", "excerpt")})
            return 200, {}, self._j(self.posts[pid])
        if method == "DELETE":
            if query.get("force") == "true":
                del self.posts[pid]
                return 200, {}, self._j({"deleted": True})
            self.posts[pid]["status"] = "trash"
            return 200, {}, self._j(self.posts[pid])
        return 405, {}, b"{}"

    def _media(self, method, pid, sub, payload, query, headers, body):
        if method == "POST" and not pid:
            self.next_id += 1
            name = re.search(r'filename="([^"]+)"', headers.get("Content-Disposition", ""))
            self.media[self.next_id] = {"id": self.next_id, "file": name.group(1) if name else "", "bytes": body or b"",
                                        "type": headers.get("Content-Type", ""), "alt_text": "",
                                        "source_url": f"{self.base}/wp-content/uploads/{name.group(1) if name else 'x'}"}
            return 201, {}, self._j({k: v for k, v in self.media[self.next_id].items() if k != "bytes"})
        if method == "POST" and pid in self.media:
            self.media[pid]["alt_text"] = payload.get("alt_text", "")
            return 200, {}, self._j({"id": pid, "alt_text": self.media[pid]["alt_text"]})
        return (200, {"x-wp-total": str(len(self.media))}, self._j([])) if method == "GET" else (404, {}, b"{}")

    def _categories(self, method, pid, sub, payload, query, headers, body):
        return 200, {"x-wp-total": str(len(self.categories))}, self._j(self.categories)

    # ---- what the tests ask afterwards ----------------------------------------------------------------------------
    def writes(self):
        return [r for r in self.requests if r["method"] != "GET"]

    def ever_asked_for_status(self, *statuses) -> bool:
        return any(str(r["payload"].get("status", "")).lower() in statuses or r["query"].get("status") in statuses and r["method"] != "GET"
                   for r in self.writes())
