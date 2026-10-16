"""Builds sample decks from invented data, to look at the design (development helper)."""

import os
import sys

import deck_fixtures as fx
import decks
import sites

out = sys.argv[1] if len(sys.argv) > 1 else "preview"
os.makedirs(out, exist_ok=True)
templates = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

cases = [
    ("monthly_en", fx.monthly_report(segments=False, aio=False), sites.DeckConfig(
        enabled=True, language="en", site_label="Example", recipients=["x@example.test"])),
    ("monthly_en_segments_aio", fx.monthly_report(segments=True, aio=True), sites.DeckConfig(
        enabled=True, language="en", site_label="Example", recipients=["x@example.test"],
        brand_color="#1F3A5F", accent_color="#E07A2E")),
    ("weekly_en", fx.weekly_report(), sites.DeckConfig(
        enabled=True, language="en", site_label="Example", recipients=["x@example.test"])),
    ("monthly_fa", fx.monthly_report(segments=False, aio=False), sites.DeckConfig(
        enabled=True, language="fa", site_label="نمونه", recipients=["x@example.test"])),
]
for name, report, cfg in cases:
    with open(os.path.join(out, f"{name}.pptx"), "wb") as f:
        f.write(decks.build_deck(report, cfg, templates))
    print("built", name)
