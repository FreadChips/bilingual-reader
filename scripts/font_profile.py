"""Survey a PDF's typography: the first step in writing extraction rules for a new book.

Usage: .venv/Scripts/python.exe -I tools/font_profile.py BOOK.pdf [first_page last_page]

Prints, for a sample of pages (default: 40 pages from the middle of the book):
  - the outline (chapter detection usually comes from here)
  - every (font, size) pair with its share of the text, where it sits on the page and sample lines,
    which is what classify_line in extract.py turns into roles (body, h1, h2, caption, box, margin ...)
  - the left edges of text lines on odd and even pages (column and margin positions)
  - the offset between PDF page index and printed page number (PAGE_OFFSET)
  - fonts whose text looks like digits/punctuation standing in for math symbols (SYMBOL_FONTS)
"""
import re
import sys
from collections import Counter, defaultdict

import pymupdf


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    doc = pymupdf.open(sys.argv[1])
    n = doc.page_count
    if len(sys.argv) >= 4:
        pages = range(int(sys.argv[2]), int(sys.argv[3]) + 1)
    else:
        pages = range(max(0, n // 2 - 20), min(n, n // 2 + 20))

    print(f"# {sys.argv[1]}: {n} pages, sampling pdf pages {pages.start}-{pages.stop - 1}\n")
    print("## Outline (first 25 entries)")
    for lvl, title, page in doc.get_toc()[:25]:
        print(f"{'  ' * (lvl - 1)}- {title}  (p.{page})")

    chars, lines, samples = Counter(), Counter(), defaultdict(list)
    xs, ys = defaultdict(list), defaultdict(list)
    left = {0: Counter(), 1: Counter()}
    offsets, digitish, spans_per_font = Counter(), Counter(), Counter()
    for pno in pages:
        page = doc[pno]
        h = page.rect.height
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                spans = [s for s in l["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                main = max(spans, key=lambda s: len(s["text"]))
                key = (main["font"], round(main["size"], 1))
                chars[key] += len(text)
                lines[key] += 1
                xs[key].append(l["bbox"][0])
                ys[key].append(l["bbox"][1] / h)
                if len(samples[key]) < 3 and len(text) > 3:
                    samples[key].append(text[:70])
                left[pno % 2][round(l["bbox"][0] / 4) * 4] += 1
                # a bare number near the top or bottom edge is probably the folio
                if re.fullmatch(r"\d{1,4}", text) and (l["bbox"][1] < 0.1 * h or l["bbox"][1] > 0.88 * h):
                    offsets[pno - int(text)] += 1
                for s in spans:  # operators drawn with digit code points look like "x 5 y"
                    spans_per_font[s["font"]] += 1
                    if re.fullmatch(r"[\d,.\s]{1,3}", s["text"].strip()) and not re.search(r"\d{2}", s["text"]):
                        digitish[s["font"]] += 1

    total = sum(chars.values())
    print("\n## Fonts by share of text  (font, size: share, lines, left-x range, vertical position, samples)")
    for key, c in chars.most_common():
        if c / total < 0.002 and lines[key] < 3:
            continue
        x = sorted(xs[key])
        y = sorted(ys[key])
        where = "top" if y[len(y) // 2] < 0.1 else "bottom" if y[len(y) // 2] > 0.9 else "body"
        print(f"- {key[0]} {key[1]}: {100 * c / total:.1f}%, {lines[key]} lines, x {x[0]:.0f}–{x[-1]:.0f}, {where}")
        for s in samples[key]:
            print(f"      “{s}”")

    for parity, name in ((1, "odd"), (0, "even")):
        top = ", ".join(f"{x}:{c}" for x, c in sorted(left[parity].most_common(6)))
        print(f"\n## Common left edges on {name} pdf pages: {top}")
    if offsets:
        off, votes = offsets.most_common(1)[0]
        print(f"\n## PAGE_OFFSET (pdf index − printed number) ≈ {off}  ({votes} folios agree)")
    # a symbol font is one whose spans are mostly lone digits; body fonts only occasionally have one
    suspects = sorted(((c / spans_per_font[f], f, c) for f, c in digitish.items() if c / spans_per_font[f] >= 0.3),
                      reverse=True)
    if suspects:
        print("\n## Fonts whose spans are mostly lone digits/punctuation (math symbols stored as digits?):")
        for _, f, c in suspects:
            print(f"- {f}: {c} of {spans_per_font[f]} spans")


if __name__ == "__main__":
    main()
