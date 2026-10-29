"""List the named glyphs of a PDF's math fonts, with example lines, to write layout.glyph_maps.

    python pdf_glyphs.py BOOK.pdf [--fonts Math] [--names H9266,H11001] [--first N --last M]

Prints one row per (font, glyph name): how often it occurs and up to three lines in which the glyph
is shown as «name». Fonts are matched by a substring of their name (default "Math"). Decide each
symbol from the example lines (render the page when unsure), then add {name: symbol} to glyph_maps.
"""
import argparse
import collections
import sys

import pymupdf

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from pdf_layout import font_glyphs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--fonts", default="Math")
    ap.add_argument("--names", default="")
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--last", type=int, default=None)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    want = set(filter(None, a.names.split(",")))
    doc = pymupdf.open(a.pdf)
    count, examples = collections.Counter(), collections.defaultdict(list)
    for pno in range(a.first, a.last + 1 if a.last is not None else len(doc)):
        page = doc[pno]
        fonts = collections.defaultdict(list)
        for xref, _, _, base, *_ in page.get_fonts():
            if a.fonts in base:
                fonts[base.split("+")[-1]].append(font_glyphs(doc, xref))
        if not fonts:
            continue
        gids = {(round(c[2][0], 1), round(c[2][1], 1)): c[1] for s in page.get_texttrace() for c in s["chars"]}
        for b in page.get_text("rawdict")["blocks"]:
            for l in b.get("lines", []):
                chars = [(s, c) for s in l["spans"] for c in s["chars"]]
                for k, (s, c) in enumerate(chars):
                    name = s["font"].split("+")[-1]
                    tables = fonts.get(name) or [t for n, ts in fonts.items() if len(name) >= 24 and n.startswith(name)
                                                 for t in ts]
                    gid = gids.get((round(c["origin"][0], 1), round(c["origin"][1], 1)))
                    hits = {t[ord(c["c"])][0] for t in tables if ord(c["c"]) in t
                            and (gid is None or t[ord(c["c"])][1] == gid)}
                    if len(hits) != 1:
                        continue
                    g = hits.pop()
                    if want and g not in want:
                        continue
                    key = (name, g)
                    count[key] += 1
                    if len(examples[key]) < 3:
                        line = "".join(f"«{g}»" if j == k else x["c"] for j, (_, x) in enumerate(chars))
                        examples[key].append(f"p{pno}: {line[:100]!r}")
    for key in sorted(count, key=lambda k: (k[0], k[1])):
        print(f"{key[0]:28} {key[1]:10} {count[key]:5}")
        for e in examples[key]:
            print("      " + e)


if __name__ == "__main__":
    main()
