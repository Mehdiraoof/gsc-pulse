"""
PulseO: infographics made by code
=================================
Every article gets at least one infographic. It is made by code, never by an image model (those garble the text, Persian above
all): a template filled with the 3 to 6 short points the draft planned, in the brand of the site, as HTML that a renderer turns
into a PNG 1200 px wide and under 150 KB.

Templates: steps, comparison, pros and cons, price table, checklist, key takeaways, and a general facts card that fits almost
any article. The slot in the draft is [Infographic: template | title | point; point; point | numbers]. A number is only
drawn if it is in the article. Persian cards are right to left with the Vazirmatn font.

The renderer is a small interface (`render(html, width) -> PNG bytes`): the real one is a headless browser (Chromium through
Playwright) because only a browser shapes Persian text right; tests use a fake one. When an infographic cannot be made the
publishing flow leaves the planned box with its description for the reviewer.
"""

import html
import re
import struct
from dataclasses import dataclass

WIDTH = 1200
MAX_BYTES = 150_000
PLACEHOLDER = {"primary": "#1f4e79", "accent": "#e07b00", "text": "#1d2a35", "background": "#ffffff"}
TEMPLATES = {"steps": "steps", "comparison": "comparison", "pros and cons": "pros_cons", "pros_cons": "pros_cons",
             "price table": "price_table", "price or range table": "price_table", "price_table": "price_table",
             "checklist": "checklist", "key takeaways": "takeaways", "takeaways": "takeaways", "facts card": "facts",
             "facts": "facts"}


@dataclass
class Brand:
    name: str = ""
    primary: str = PLACEHOLDER["primary"]
    accent: str = PLACEHOLDER["accent"]
    text: str = PLACEHOLDER["text"]
    background: str = PLACEHOLDER["background"]
    logo_data_uri: str = ""            # a data: address of the logo, or ""
    language: str = "en"

    @property
    def rtl(self) -> bool:
        return self.language == "fa"

    @property
    def font(self) -> str:
        return "Vazirmatn, 'Noto Naskh Arabic', Tahoma, sans-serif" if self.rtl else "Inter, 'Helvetica Neue', Arial, sans-serif"


def brand_for(site, language: str = "") -> Brand:
    """The brand of a site from its deck settings (colours and logo); the placeholder colours stay where the owner has not
    sent his own yet."""
    deck = site.deck
    return Brand(name=deck.site_label or site.name, primary=deck.brand_color or PLACEHOLDER["primary"],
                 accent=deck.accent_color or PLACEHOLDER["accent"], language=language or deck.language)


def template_name(name: str) -> str:
    return TEMPLATES.get(re.sub(r"\s+", " ", (name or "").strip().lower().replace("-", " ")), "facts")


def e(text: str) -> str:
    return html.escape(str(text), quote=True)


NUMBER = re.compile(r"\d[\d.,]*")
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
TO_PERSIAN = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def digits(text, brand) -> str:
    """The text with Persian digits on a Persian card (everywhere: title, points, numbers, step numbers, footer); unchanged otherwise."""
    return str(text).translate(TO_PERSIAN) if brand.rtl else str(text)


def numbers_of(text: str) -> set:
    return {n.strip(".,") for n in NUMBER.findall(str(text or "").translate(PERSIAN_DIGITS)) if n.strip(".,")}


def keep_numbers_in_article(spec: dict, article_text: str) -> tuple:
    """(spec without the numbers that are not in the article, the numbers that were dropped). A card only shows numbers of
    the article."""
    known = numbers_of(article_text)
    kept, dropped = [], []
    for part in [p.strip() for p in re.split(r"[;,،]", spec.get("numbers", "")) if p.strip()]:
        (kept if numbers_of(part) <= known and numbers_of(part) else dropped).append(part)
    return dict(spec, numbers="; ".join(kept)), dropped


# --------------------------------------------------------------------------- #
# The templates
# --------------------------------------------------------------------------- #

VAZIRMATN_CSS = "https://cdn.jsdelivr.net/npm/vazirmatn@33.0.3/Vazirmatn-font-face.css"


def _page(spec: dict, brand: Brand, body: str) -> str:
    direction = "rtl" if brand.rtl else "ltr"
    font_import = f'@import url("{VAZIRMATN_CSS}");' if brand.rtl else ""        # the browser fetches Vazirmatn when it is not installed
    logo = f'<div class="top"><img src="{e(brand.logo_data_uri)}" style="height:44px"></div>' if brand.logo_data_uri else ""      # the name is in the footer only
    return f"""<!doctype html><html lang="{brand.language}" dir="{direction}"><head><meta charset="utf-8"><style>
{font_import}
*{{box-sizing:border-box}} body{{margin:0;background:{brand.background};font-family:{brand.font}}}
#card{{width:{WIDTH}px;padding:56px 64px;background:{brand.background};color:{brand.text}}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:34px;color:{brand.primary};font-size:26px;font-weight:700}}
h1{{margin:0 0 34px;font-size:54px;line-height:1.25;color:{brand.primary}}}
.row{{display:flex;align-items:center;gap:22px;margin:0 0 18px;padding:22px 26px;border-radius:18px;background:#f3f6fa;font-size:32px;line-height:1.5}}
.n{{flex:none;width:58px;height:58px;border-radius:50%;background:{brand.accent};color:#fff;font-weight:700;text-align:center;line-height:58px;font-size:30px}}
.col{{flex:1}} .head{{font-weight:700;color:#fff;background:{brand.primary}}} .pro{{border-inline-start:10px solid #2e8b57}}
.con{{border-inline-start:10px solid #c0392b}} .chip{{display:inline-block;margin:6px 10px 6px 0;padding:10px 22px;border-radius:30px;
background:{brand.accent};color:#fff;font-size:34px;font-weight:700}} .price{{font-weight:700;color:{brand.primary};white-space:nowrap}}
.foot{{margin-top:24px;font-size:22px;color:#6b7785}}
</style></head><body><div id="card">{logo}<h1>{e(spec['title'])}</h1>{body}
<div class="foot">{e(digits(brand.name, brand))}</div></div></body></html>"""


def _split(point: str, sep: str) -> tuple:
    left, _, right = point.partition(sep)
    return left.strip(), right.strip()


def steps(spec, brand):
    return "".join(f'<div class="row"><div class="n">{digits(i, brand)}</div><div class="col">{e(p)}</div></div>' for i, p in enumerate(spec["points"], 1))


def comparison(spec, brand):
    heads = _split(spec["title"], " vs ") if " vs " in spec["title"] else ("", "")
    head = f'<div class="row head"><div class="col">{e(heads[0])}</div><div class="col">{e(heads[1])}</div></div>' if heads[0] else ""
    rows = "".join(f'<div class="row"><div class="col">{e(a)}</div><div class="col">{e(b)}</div></div>'
                   for a, b in (_split(p, " vs ") for p in spec["points"]))
    return head + rows


def pros_cons(spec, brand):
    out = []
    for p in spec["points"]:
        kind = "con" if re.match(r"\s*(con|cons|-|ایراد|نقطه ضعف)\s*:", p, re.I) else "pro"
        out.append(f'<div class="row {kind}"><div class="col">{e(re.sub(r"^\s*[^:]{1,12}:\s*", "", p) if ":" in p[:14] else p)}</div></div>')
    return "".join(out)


def price_table(spec, brand):
    rows = []
    for p in spec["points"]:
        label, _, price = p.rpartition(":")
        rows.append(f'<div class="row"><div class="col">{e((label or p).strip())}</div><div class="price">{e(price.strip() if label else "")}</div></div>')
    return "".join(rows)


def checklist(spec, brand):
    return "".join(f'<div class="row"><div class="n">✓</div><div class="col">{e(p)}</div></div>' for p in spec["points"])


def takeaways(spec, brand):
    return "".join(f'<div class="row"><div class="n">{digits(i, brand)}</div><div class="col"><b>{e(p)}</b></div></div>' for i, p in enumerate(spec["points"], 1))


def facts(spec, brand):
    chips = "".join(f'<span class="chip">{e(n)}</span>' for n in re.split(r"[;،]", spec.get("numbers", "")) if n.strip())
    return (f"<div>{chips}</div>" if chips else "") + "".join(f'<div class="row"><div class="col">{e(p)}</div></div>' for p in spec["points"])


BUILDERS = {"steps": steps, "comparison": comparison, "pros_cons": pros_cons, "price_table": price_table, "checklist": checklist,
            "takeaways": takeaways, "facts": facts}


def _localised(spec: dict, brand: Brand) -> dict:
    """A copy of the plan whose texts have Persian digits when the card is Persian."""
    if not brand.rtl:
        return spec
    return dict(spec, title=digits(spec.get("title", ""), brand), numbers=digits(spec.get("numbers", ""), brand),
                points=[digits(p, brand) for p in spec.get("points", [])])


def build_html(spec: dict, brand: Brand) -> str:
    """The card as HTML. 3 to 6 points are drawn (a plan with fewer than 3 or more than 6 is rejected by `valid`)."""
    spec = _localised(spec, brand)
    return _page(spec, brand, BUILDERS[template_name(spec.get("template", ""))](spec, brand))


def valid(spec: dict) -> str:
    """"" when the plan can be drawn, else why not."""
    if not (spec.get("title") or "").strip():
        return "no title"
    if not 3 <= len(spec.get("points") or []) <= 6:
        return "an infographic needs 3 to 6 points"
    return ""


# --------------------------------------------------------------------------- #
# The picture
# --------------------------------------------------------------------------- #

def png_size(data: bytes) -> tuple:
    """(width, height) of a PNG, or (0, 0) when it is not one."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return 0, 0
    return struct.unpack(">II", data[16:24])


def check_png(data: bytes) -> str:
    """"" when the picture is a PNG 1200 px wide and under 150 KB, else why not."""
    width, _ = png_size(data)
    if not width:
        return "the renderer did not give a PNG"
    if width != WIDTH:
        return f"the picture is {width} px wide, not {WIDTH}"
    if len(data) > MAX_BYTES:
        return f"the picture is {len(data) // 1000} KB, more than {MAX_BYTES // 1000} KB"
    return ""


def file_name(slug: str, number: int) -> str:
    """An English slug style name: lower case letters, digits and hyphens."""
    base = re.sub(r"[^a-z0-9]+", "-", (slug or "article").lower()).strip("-") or "article"
    return f"{base}-infographic-{number}.png"


class ChromiumRenderer:
    """The real renderer: a headless Chromium (Playwright) screenshots the card. Only a browser shapes Persian text correctly.
    Needs `pip install playwright` and `playwright install chromium`; the fonts (Vazirmatn) must be installed on the machine."""

    def render(self, page_html: str, width: int = WIDTH) -> bytes:
        from playwright.sync_api import sync_playwright          # imported here: tests and runs without it never need it
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": width, "height": 800})
                page.set_content(page_html, wait_until="networkidle")
                page.evaluate("document.fonts.ready")             # the fonts are there before the picture is taken
                return page.locator("#card").screenshot(type="png")
            finally:
                browser.close()


def shrink_png(data: bytes, colours: int = 96) -> bytes:
    """The same picture with a palette of fewer colours (flat cards lose nothing the eye can see): for a PNG that is over the size
    limit. Needs Pillow (python-pptx brings it); the picture is returned as it is when Pillow is not there."""
    try:
        import io
        from PIL import Image
    except ImportError:
        return data
    image = Image.open(io.BytesIO(data)).convert("RGB").quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_card(renderer, page_html: str) -> bytes:
    """The card as a PNG within the limits: shrunk once when it is over the size limit. Check the result with check_png."""
    data = renderer.render(page_html, WIDTH)
    if png_size(data)[0] == WIDTH and len(data) > MAX_BYTES:
        data = shrink_png(data)
    return data


SAMPLES = {
    "en": {"template": "steps", "title": "How to plant a seed", "numbers": "4 steps",
           "points": ["Prepare the soil and make a small hole", "Place the seed and cover it lightly", "Water it gently every day",
                      "Keep it in the light and wait"]},
    "fa": {"template": "steps", "title": "چگونه یک بذر بکاریم", "numbers": "۴ مرحله",
           "points": ["خاک را آماده کنید و یک گودال کوچک بکنید", "بذر را بگذارید و روی آن را کم بپوشانید",
                      "هر روز کم کم آب بدهید", "آن را در نور نگه دارید و صبر کنید"]},
}
