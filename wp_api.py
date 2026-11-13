"""
PulseO: writing to WordPress, only the ways the publishing flow needs
=====================================================================
WordPress core only: the REST API with an Application Password, no plugin. What this client can do, and nothing else:

  * read a post (with its content as written, its revisions' count, its categories and featured image)
  * make a DRAFT post (status always "draft")
  * upload a file to the media library
  * copy title, content and excerpt into an existing post (never its status, slug, date, author, featured image, comments)
  * move a draft of its own to the trash (never a published post, never for good)

It can never publish: a request that would set a status of publish, future, private or pending is refused here, before it is sent,
and the same for deleting a published post or deleting for good. It never touches settings, plugins, themes, menus or users:
there is no method for them. Every write is handed to `log(action, post_id)` (the WordPress Log tab of the site's dashboard).
Secrets never appear in a message: errors say the status and the kind of error, not the address.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

import wp_check

UPDATABLE = ("title", "content", "excerpt")
FORBIDDEN_STATUSES = ("publish", "future", "private", "pending")


class WpError(Exception):
    """A request failed or was refused. The message is safe to print."""


def default_request(method: str, url: str, headers: dict, body: bytes = None) -> tuple:
    """(status, headers, body); status 0 when there was no answer. Never raises."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=wp_check.TIMEOUT_S) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read(5_000_000)
    except urllib.error.HTTPError as e:
        try:
            data = e.read(200_000)
        except OSError:
            data = b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, data
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}, b""


class Wp:
    def __init__(self, install, request=default_request, log=lambda action, post_id: None):
        self.base, self._request, self._log = install.base, request, log
        self._auth = "Basic " + base64.b64encode(f"{install.user}:{install.password}".encode()).decode()

    # ---- the one place every request goes through -----------------------------------------------------------------
    def _call(self, method: str, route: str, payload: dict = None, params: dict = None, raw: bytes = None,
              content_type: str = "application/json", extra_headers: dict = None) -> tuple:
        headers = {"Authorization": self._auth, "Accept": "application/json", "User-Agent": wp_check.UA}
        headers.update(extra_headers or {})
        body = None
        if raw is not None:
            headers["Content-Type"], body = content_type, raw
        elif payload is not None:
            headers["Content-Type"], body = "application/json", json.dumps(payload).encode()
        query = urllib.parse.urlencode(params or {})
        first = f"{self.base}/wp-json{route}" + (f"?{query}" if query else "")
        second = f"{self.base}/?rest_route={route}" + (f"&{query}" if query else "")
        status, headers_back, data = self._request(method, first, headers, body)
        if status == 404 and method == "GET":
            status, headers_back, data = self._request(method, second, headers, body)
        try:
            parsed = json.loads(data.decode("utf-8", errors="replace")) if data else None
        except ValueError:
            parsed = None
        return status, headers_back, parsed

    @staticmethod
    def _refuse_publishing(payload: dict):
        status = str((payload or {}).get("status", "")).lower()
        if status in FORBIDDEN_STATUSES:
            raise WpError(f"refused: this tool never sets the status {status}")

    def _check(self, what: str, status: int, data):
        if status == 0:
            raise WpError(f"{what}: no answer")
        if not 200 <= status < 300:
            code = data.get("code") if isinstance(data, dict) else ""
            raise WpError(f"{what}: HTTP {status}" + (f" ({code})" if code else ""))
        return data

    # ---- reading -------------------------------------------------------------------------------------------------
    def read_post(self, post_id: int) -> dict:
        """The post with its raw content (context=edit): title, content, excerpt, status, modified date, categories, featured media."""
        status, _, data = self._call("GET", f"/wp/v2/posts/{int(post_id)}", params={"context": "edit"})
        return self._check("reading a post", status, data)

    def find_by_slug(self, slug: str, status: str = "any") -> dict:
        status_code, _, data = self._call("GET", "/wp/v2/posts", params={"slug": slug, "status": status, "context": "edit"})
        posts = self._check("looking for a post", status_code, data)
        return posts[0] if posts else {}

    def published_since(self, iso_day: str) -> list:
        """The posts published after a day (id, title, link, raw content): for the marker check."""
        out, page = [], 1
        while page <= 20:
            status, _, data = self._call("GET", "/wp/v2/posts", params={"status": "publish", "after": iso_day,
                                                                       "per_page": 50, "page": page, "context": "edit"})
            if status == 400:                                   # past the last page
                break
            batch = self._check("reading the published posts", status, data)
            out += batch
            if len(batch) < 50:
                break
            page += 1
        return out

    # ---- writing -------------------------------------------------------------------------------------------------
    def create_draft(self, title: str, content: str, slug: str = "", excerpt: str = "", categories: list = None) -> dict:
        payload = {"status": "draft", "title": title, "content": content}
        if slug:
            payload["slug"] = slug
        if excerpt:
            payload["excerpt"] = excerpt
        if categories:
            payload["categories"] = list(categories)
        self._refuse_publishing(payload)
        status, _, data = self._call("POST", "/wp/v2/posts", payload)
        post = self._check("making a draft", status, data)
        self._log("created a draft", post.get("id", 0))
        return post

    def upload_media(self, filename: str, data: bytes, alt: str = "", content_type: str = "image/png") -> dict:
        status, _, answer = self._call("POST", "/wp/v2/media", raw=data, content_type=content_type,
                                       extra_headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        media = self._check("uploading a file", status, answer)
        if alt:
            status, _, answer = self._call("POST", f"/wp/v2/media/{int(media['id'])}", {"alt_text": alt})
            media = {**media, **(self._check("writing the alt text", status, answer) or {})}
        self._log("uploaded a file", media.get("id", 0))
        return media

    def apply_to_post(self, post_id: int, fields: dict) -> dict:
        """Copies title, content and/or excerpt into a post. Nothing else can be sent."""
        extra = set(fields) - set(UPDATABLE)
        if extra:
            raise WpError(f"refused: this tool only changes {', '.join(UPDATABLE)}")
        self._refuse_publishing(fields)
        status, _, data = self._call("POST", f"/wp/v2/posts/{int(post_id)}", dict(fields))
        post = self._check("updating a post", status, data)
        self._log("copied an update into the post", post_id)
        return post

    def trash_draft(self, post_id: int) -> None:
        """Moves a DRAFT to the trash (WordPress keeps it there). A post that is not a draft is never touched."""
        post = self.read_post(post_id)
        if post.get("status") != "draft":
            raise WpError("refused: only a draft can be moved to the trash")
        status, _, data = self._call("DELETE", f"/wp/v2/posts/{int(post_id)}")          # no force: the trash, not for good
        self._check("moving a draft to the trash", status, data)
        self._log("moved a draft to the trash", post_id)

    def revision_count(self, post_id: int) -> int:
        status, headers, data = self._call("GET", f"/wp/v2/posts/{int(post_id)}/revisions", params={"per_page": 1, "_fields": "id"})
        self._check("reading the revisions", status, data)
        try:
            return int(headers.get("x-wp-total", ""))
        except ValueError:
            return len(data or [])
