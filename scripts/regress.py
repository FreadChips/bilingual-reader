"""Regression check for extraction changes: snapshot the extracted chapters, re-extract, compare.

Usage: python regress.py [--book DIR] save        copy build/source/ch*.json to build/snapshot/
       python regress.py [--book DIR] compare     report every chapter that changed since the snapshot

Run `save` before changing book.yaml rules, patches or the toolkit, then re-extract all finished
chapters (extract.py all) and run `compare`. A changed sentence means its annotation may no longer
fit; a changed paragraph id means annotations keyed by that id point at the wrong paragraph.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I, which leaves out the script's folder
from bookkit import open_book  # noqa: E402


def sentences(data):
    out = {}

    def walk(blocks):
        for b in blocks:
            for s in b.get("sentences", []):
                out[s["id"]] = (b["type"], s["html"])
            if b["type"] == "box":
                walk(b["blocks"])
    walk(data["blocks"])
    walk(data.get("appendix", []))
    return out


def layout(data):
    """Block ids in reading order, to catch figures that moved."""
    out = []

    def walk(blocks):
        for b in blocks:
            out.append(b.get("id", b["type"]))
            if b["type"] == "box":
                walk(b["blocks"])
    walk(data["blocks"])
    walk(data.get("appendix", []))
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    book, args = open_book(sys.argv[1:])
    snap = book.root / "build" / "snapshot"
    if args == ["save"]:
        snap.mkdir(parents=True, exist_ok=True)
        files = sorted(book.src_dir.glob("ch*.json"))
        for f in files:
            shutil.copy2(f, snap / f.name)
        print(f"saved {len(files)} chapters to {snap}")
        return
    if args != ["compare"]:
        sys.exit(__doc__)
    changed = 0
    for old_f in sorted(snap.glob("ch*.json")):
        new_f = book.src_dir / old_f.name
        if not new_f.exists():
            print(f"{old_f.stem}: missing from build/source")
            changed += 1
            continue
        if old_f.read_bytes() == new_f.read_bytes():
            continue
        changed += 1
        old, new = json.loads(old_f.read_text(encoding="utf-8")), json.loads(new_f.read_text(encoding="utf-8"))
        a, b = sentences(old), sentences(new)
        print(f"== {old_f.stem}")
        for k in a:
            if a[k] != b.get(k):
                print(f"  {k}\n    OLD {a[k]}\n    NEW {b.get(k)}")
        if set(a) ^ set(b):
            print(f"  sentence ids added/removed: {sorted(set(a) ^ set(b))}")
        if layout(old) != layout(new):
            print("  block order changed (figures, boxes or headings moved)")
        for key in ("glossary", "photos", "end"):
            if old.get(key) != new.get(key):
                print(f"  {key} changed")
    print(f"{changed} chapter(s) differ from the snapshot" if changed else "no changes")


if __name__ == "__main__":
    main()
