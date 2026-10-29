"""Draw the automatic engine's region map over the PDF pages, for checking a chapter by eye.

Usage: python overlay.py [--book DIR] 3                 every page of chapter 3
       python overlay.py [--book DIR] 3 41 42           some PDF pages (1-based, as in region ids)
       python overlay.py [--book DIR] 3 --review        only pages with review notes

Reads build/layout/ch03.json (written by extract.py) and writes build/layout/ch03/p0041.png ...
Each region gets a coloured frame by role, and a tag "r5 body #3": region id, role, reading position.
Units are framed again in their own colour: figure (green), box (orange), photo (teal). Grey
dashed frames are tinted panels. A hand-labelled region's tag ends with *.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I, which leaves out the script's folder
import pymupdf  # noqa: E402

from bookkit import open_book  # noqa: E402

COLORS = {
    "body": (0.15, 0.35, 0.85), "list": (0.15, 0.35, 0.85), "quote": (0.45, 0.25, 0.75),
    "heading": (0.85, 0.1, 0.1), "margin": (0.85, 0.45, 0.0), "caption": (0.0, 0.55, 0.55),
    "picture": (0.1, 0.6, 0.2), "formula": (0.6, 0.0, 0.6), "skip": (0.6, 0.6, 0.6),
    "dropcap": (0.6, 0.6, 0.6), "panel": (0.5, 0.5, 0.5),
}
UNIT_COLORS = {"figure": (0.1, 0.6, 0.2), "box": (0.95, 0.55, 0.0), "photo": (0.0, 0.55, 0.55)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    book, args = open_book(sys.argv[1:])
    review = "--review" in args
    args = [a for a in args if a != "--review"]
    if not args:
        sys.exit(__doc__)
    cid = f"ch{int(args[0]):02d}"
    path = book.root / "build" / "layout" / f"{cid}.json"
    if not path.exists():
        sys.exit(f"{path} not found: run extract.py {int(args[0])} first (automatic engine)")
    data = json.loads(path.read_text(encoding="utf-8"))
    pages = [int(a) for a in args[1:]] or [int(p) for p in data["pages"]]
    if review:
        pages = sorted({n["page"] for n in data["notes"]} & set(pages))
    doc = pymupdf.open(book.source)
    out = book.root / "build" / "layout" / cid
    out.mkdir(parents=True, exist_ok=True)
    for pnum in pages:
        regions = data["pages"].get(str(pnum))
        if regions is None:
            print(f"page {pnum} is not in {cid}")
            continue
        tmp = pymupdf.open()
        tmp.insert_pdf(doc, from_page=pnum - 1, to_page=pnum - 1)
        page = tmp[0]
        units = {}
        for r in regions:
            rect = pymupdf.Rect(r["bbox"])
            color = COLORS.get(r["role"], (0, 0, 0))
            if r["role"] == "panel":
                page.draw_rect(rect + (-3, -3, 3, 3), color=color, width=0.8, dashes="[3] 0")
                continue
            page.draw_rect(rect, color=color, width=0.9)
            if r.get("unit"):
                units.setdefault(r["unit"], pymupdf.Rect(rect))
                units[r["unit"]] |= rect
            tag = f"{r['id'].split('.')[1]} {r['role']}{r.get('level') or '' if r['role'] == 'heading' else ''}"
            if r.get("order"):
                tag += f" #{r['order']}"
            if r.get("labelled"):
                tag += " *"
            w = pymupdf.get_text_length(tag, fontsize=5.5) + 3
            box = pymupdf.Rect(rect.x0, rect.y0 - 7, rect.x0 + w, rect.y0)
            page.draw_rect(box, color=None, fill=color)
            page.insert_text((box.x0 + 1.5, box.y1 - 1.5), tag, fontsize=5.5, color=(1, 1, 1))
        for name, rect in units.items():
            kind = name.rstrip("0123456789")
            page.draw_rect(rect + (-2, -2, 2, 2), color=UNIT_COLORS.get(kind, (0, 0, 0)), width=2)
            page.insert_text((rect.x1 - 40, rect.y1 + 8), name, fontsize=7, color=UNIT_COLORS.get(kind, (0, 0, 0)))
        notes = [n for n in data["notes"] if n["page"] == pnum]
        for k, n in enumerate(notes[:6]):
            page.insert_text((10, page.rect.height - 8 - 8 * k), f"! {n['region']}: {n['note']}"[:120],
                             fontsize=6, color=(0.8, 0, 0))
        f = out / f"p{pnum:04d}.png"
        page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(f)
        print(f)


if __name__ == "__main__":
    main()
