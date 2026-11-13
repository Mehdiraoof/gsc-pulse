"""
PulseO: WordPress, the read-only access check (publishing job, first step)
=========================================================================
Before anything is written to a WordPress site, this checks, with GET requests only, that the site can be reached and that
the account made for PulseO (an Application Password of an Editor, WordPress core only, no plugin) can read what the
publishing flow needs: the REST API, the user's own role and rights, the drafts, the media library, the published posts and
the categories. It never writes, never publishes and prints counts only: no site address, no title, no user name.

A site can have several WordPress installs (Turkey Guzel: English at the root, Persian in /fa). The secrets of an install
are named from the site and the language: WP_SITE1_USER and WP_SITE1_APP_PASSWORD for a site with one install,
WP_SITE4_EN_USER / WP_SITE4_EN_APP_PASSWORD and WP_SITE4_FA_USER / WP_SITE4_FA_APP_PASSWORD for one with two languages.
"""

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

import site_check

TIMEOUT_S = 30
UA = site_check.BROWSER_AGENT
CAN = ("edit_posts", "upload_files", "publish_posts", "edit_others_posts")


@dataclass
class Install:
    site: str                 # the neutral name of the site (site-1)
    label: str                # "" for a site with one install, else the language code in capitals (EN, FA)
    base: str                 # the address of the install without a slash at the end
    prefix: str               # WP_SITE4_EN
    user: str = ""
    password: str = ""

    @property
    def secrets(self) -> tuple:
        return f"{self.prefix}_USER", f"{self.prefix}_APP_PASSWORD"

    @property
    def name(self) -> str:
        return f"{self.site} {self.label}".strip()


def secret_prefix(site_name: str, label: str = "") -> str:
    return "WP_" + re.sub(r"[^A-Z0-9]", "", site_name.upper()) + (f"_{label.upper()}" if label else "")


def installs_for(site, env) -> list:
    """The WordPress installs of a site: one for the whole site, or one for each language folder of a two-language site."""
    origin = site_check.origin_of(site.site_url)
    languages = getattr(getattr(site, "content", None), "languages", {}) or {}
    pairs = [(code.upper(), origin + folder) for code, folder in languages.items()] if languages else [("", origin)]
    out = []
    for label, base in pairs:
        prefix = secret_prefix(site.name, label)
        out.append(Install(site.name, label, base.rstrip("/"), prefix, (env.get(f"{prefix}_USER") or "").strip(),
                           (env.get(f"{prefix}_APP_PASSWORD") or "").replace(" ", "")))
    return out


def default_opener(url: str, headers: dict) -> tuple:
    """(status, headers, body) of a GET; status 0 when there was no answer. Never raises, never shows the address."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read(2_000_000)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(200_000)
        except OSError:
            body = b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}, b""


class Client:
    """GET requests to one install. The REST API is asked at /wp-json/..., and at /?rest_route=/... when the site has no
    pretty links. The password is only ever in the Authorization header."""

    def __init__(self, install: Install, opener=default_opener):
        self.base, self._open = install.base, opener
        self._auth = ""
        if install.user and install.password:
            self._auth = "Basic " + base64.b64encode(f"{install.user}:{install.password}".encode()).decode()

    def get(self, route: str, params: dict = None, auth: bool = True) -> tuple:
        """(status, headers, parsed JSON or None)."""
        headers = {"Accept": "application/json", "User-Agent": UA}
        if auth and self._auth:
            headers["Authorization"] = self._auth
        query = urllib.parse.urlencode(params or {})
        for url in (f"{self.base}/wp-json{route}" + (f"?{query}" if query else ""),
                    f"{self.base}/?rest_route={route}" + (f"&{query}" if query else "")):
            status, response, body = self._open(url, headers)
            if status != 404:
                break
        try:
            data = json.loads(body.decode("utf-8", errors="replace")) if body else None
        except ValueError:
            data = None
        return status, response, data


def _total(response: dict) -> int:
    try:
        return int(response.get("x-wp-total", ""))
    except ValueError:
        return -1


def check_install(install: Install, opener=default_opener, today: date = None) -> list:
    """The lines for one install: counts, yes and no, never an address or a name."""
    today = today or date.today()
    name = install.name
    if not (install.user and install.password):
        return [f"{name}: no login yet: create the secrets {install.secrets[0]} and {install.secrets[1]}"]
    client = Client(install, opener)
    status, _, root = client.get("/", auth=False)
    if status == 0:
        return [f"{name}: no answer (the server may block requests from outside its country)"]
    spaces = (root or {}).get("namespaces", []) if isinstance(root, dict) else []
    if status != 200 or "wp/v2" not in spaces:
        return [f"{name}: the REST API did not answer as WordPress does (HTTP {status})"]
    lines = [f"{name}: reachable, REST API wp/v2 answers"]
    status, _, me = client.get("/wp/v2/users/me", {"context": "edit"})
    if status != 200 or not isinstance(me, dict):
        lines.append(f"{name}: the login was refused (HTTP {status}): check the user and the Application Password")
        return lines
    caps = me.get("capabilities") or {}
    lines.append(f"{name}: login ok, role {', '.join(me.get('roles') or ['none'])}; "
                 + ", ".join(f"{c} {'yes' if caps.get(c) else 'no'}" for c in CAN))
    month = (today - timedelta(days=30)).isoformat() + "T00:00:00"
    for label, route, params in (("drafts", "/wp/v2/posts", {"status": "draft"}), ("media files", "/wp/v2/media", {}),
                                 ("published posts", "/wp/v2/posts", {"status": "publish"}),
                                 ("published in the last 30 days", "/wp/v2/posts", {"status": "publish", "after": month}),
                                 ("categories", "/wp/v2/categories", {})):
        status, response, _ = client.get(route, dict(params, per_page=1, _fields="id"))
        lines.append(f"{name}: {label} " + (str(_total(response)) if status == 200 and _total(response) >= 0
                                           else f"could not be read (HTTP {status})"))
    return lines


def check_lines(site_list: list, env, opener=default_opener, today: date = None) -> list:
    """The lines for the installs of the sites named in WORDPRESS_SITES (a line saying which secrets to create for an install that has
    no login yet); without that list, for every install that has a login saved. Other sites are not looked at."""
    wanted = {n.strip().lower() for n in (env.get("WORDPRESS_SITES") or "").split(",") if n.strip()}
    out = []
    for site in site_list:
        if wanted and site.name.lower() not in wanted:
            continue
        installs = installs_for(site, env)
        if not wanted and not any(i.user and i.password for i in installs):
            continue
        for install in installs:
            out += check_install(install, opener, today)
    return out
