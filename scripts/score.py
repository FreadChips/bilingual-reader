"""Compare this book's extraction with another one (a checked earlier extraction, or another engine's).

Usage: python score.py [--book DIR] OTHER_DIR [3 4 ...] [--show 3]

OTHER_DIR holds chNN.json files (e.g. build/snapshot, or another book folder's build/source).
Prints, per chapter and in total: sentences that match exactly (tags and spacing ignored) as recall
(share of OTHER's sentences found here) and precision (share of ours found there), and counts of
headings, figures/tables, boxes, glossary terms and photos. --show N lists chapter N's sentences
that are only here and only there, which is where the two extractions differ.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I, which leaves out the script's folder
from bookkit import open_book  # noqa: E402


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def walk(blocks, out):
    for b in blocks:
        out.append(b)
        if b["type"] == "box":
            walk(b["blocks"], out)
    return out


def stats(data):
    blocks = walk(data["blocks"] + data["appendix"], [])
    return {
        "sent": Counter(norm(s["html"]) for b in blocks for s in b.get("sentences", [])),
        "heading": sum(b["type"] == "heading" for b in blocks),
        "figure": sum(b["type"] in ("figure", "table") for b in blocks),
        "box": sum(b["type"] == "box" for b in blocks),
        "glossary": len(data["glossary"]),
        "photo": len(data["photos"]),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    book, args = open_book(sys.argv[1:])
    show = None
    if "--show" in args:
        i = args.index("--show")
        show = int(args[i + 1])
        del args[i:i + 2]
    if not args:
        sys.exit(__doc__)
    other = Path(args[0])
    nums = [int(a) for a in args[1:]] or sorted(int(p.stem[2:]) for p in book.src_dir.glob("ch*.json"))
    total = Counter()
    keys = ("heading", "figure", "box", "glossary", "photo")
    print(f"{'':6}{'recall':>8}{'precision':>10}   " + "  ".join(f"{k} here/there" for k in keys))
    for n in nums:
        cid = f"ch{n:02d}"
        a_path, b_path = book.src_dir / f"{cid}.json", other / f"{cid}.json"
        if not a_path.exists() or not b_path.exists():
            print(f"{cid}: missing {'here' if not a_path.exists() else 'there'}")
            continue
        here = stats(json.loads(a_path.read_text(encoding="utf-8")))
        there = stats(json.loads(b_path.read_text(encoding="utf-8")))
        hit = sum((here["sent"] & there["sent"]).values())
        nh, nt = sum(here["sent"].values()), sum(there["sent"].values())
        total.update({"hit": hit, "here": nh, "there": nt})
        for k in keys:
            total[k + "_here"] += here[k]
            total[k + "_there"] += there[k]
        print(f"{cid:6}{100 * hit / max(nt, 1):7.1f}%{100 * hit / max(nh, 1):9.1f}%   "
              + "  ".join(f"{here[k]:>{len(k) + 1}}/{there[k]:<{len(k) + 4}}" for k in keys))
        if show == n:
            print("\n-- only here:")
            print("\n".join("  " + s for s in (here["sent"] - there["sent"]).elements()))
            print("\n-- only there:")
            print("\n".join("  " + s for s in (there["sent"] - here["sent"]).elements()))
            print()
    if len(nums) > 1:
        print(f"{'total':6}{100 * total['hit'] / max(total['there'], 1):7.1f}%"
              f"{100 * total['hit'] / max(total['here'], 1):9.1f}%   "
              + "  ".join(f"{total[k + '_here']:>{len(k) + 1}}/{total[k + '_there']:<{len(k) + 4}}" for k in keys))


if __name__ == "__main__":
    main()
