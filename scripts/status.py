"""Where the book stands and what to do next: run it at the start of every session, by any agent.

Usage: python status.py [--book DIR]

One line per chapter: extracted sentences, open review notes, hand labels, patches, annotated
paragraphs, note density against book.yaml annotation.notes_per_100, whether check.py passes, and
the status recorded in NOTES.md (approval is the user's, so only NOTES.md says "approved").
Then the next step for the first chapter the user has not approved, and whether the EPUB is current.
"""
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I, which leaves out the script's folder
from bookkit import open_book  # noqa: E402
from check import Checker, source_paras  # noqa: E402


def notes_status(book):
    """{chapter number: status text} from the first table under '## Status' in NOTES.md."""
    f = book.root / "NOTES.md"
    out = {}
    if not f.exists():
        return out, False
    text = f.read_text(encoding="utf-8")
    m = re.search(r"^## Status\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    for line in (m.group(1) if m else "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        r = re.fullmatch(r"(\d+)(?:\s*[–-]\s*(\d+))?", cells[0])
        if r:
            for n in range(int(r.group(1)), int(r.group(2) or r.group(1)) + 1):
                out[n] = cells[1]
    return out, True


def outline_chapters(book):
    """Chapter numbers the source has, extracted or not."""
    try:
        if book.cfg["source"]["type"] == "html":
            from extract_html import items
            return items(book)
        if book.cfg["source"]["type"] == "epub":
            from extract_epub import Package, chapter_entries
            return {e[0] for e in chapter_entries(Package(book.source), book.cfg)}
        import pymupdf
        from pdf_layout import chapter_ranges
        doc = pymupdf.open(book.source)
        return {c[0] for c in chapter_ranges(doc, book.cfg["chapters"].get("outline_pattern"), book.cfg["book"]["title"])}
    except Exception as e:  # the listing is a convenience; the extracted files still tell the story
        print(f"(could not read the chapter list from the source: {e})")
        return set()


def review_count(book, cid):
    f = book.root / "build" / "layout" / f"{cid}.txt"
    if not f.exists():
        return None
    m = re.search(r"^## review\n(.*?)(?:\n\n|\Z)", f.read_text(encoding="utf-8"), re.M | re.S)
    return len([x for x in (m.group(1) if m else "").splitlines() if x.strip()])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    book, _ = open_book(sys.argv[1:])
    ann = book.cfg.get("annotation") or {}
    checker = Checker(book)
    noted, has_notes = notes_status(book)
    nums = sorted(outline_chapters(book) | checker.chapters | set(noted))
    include = book.cfg["chapters"].get("include")  # the chapters agreed to be done (a filing's Items 7 and 8)
    if include:
        nums = [n for n in nums if n in set(include)]

    title = book.cfg["book"]["title"] or book.root.name
    print(f"{title} — {book.root}")
    if not ann.get("focus") or not ann.get("notes_per_100"):
        print("! book.yaml has no agreed annotation standard (annotation: focus, notes_per_100, lang_notes):"
              " agree it with the user before chapter 1")
    if not has_notes:
        print("! no NOTES.md: create it from the skill's templates/NOTES.md")
    print(f"\n{'ch':>3} {'sentences':>9} {'review':>6} {'labels':>6} {'patches':>7} {'annotated':>11}"
          f" {'notes/100':>9}  {'check':10} NOTES.md")
    rows = {}
    for n in nums:
        cid = f"ch{n:02d}"
        src_f = book.src_dir / f"{cid}.json"
        row = {"extracted": src_f.exists(), "annotated": False, "missing": [], "check": None}
        sents = paras_total = 0
        if row["extracted"]:
            src = json.loads(src_f.read_text(encoding="utf-8"))
            ids = [b["id"] for b in source_paras(src["blocks"] + src.get("appendix", []))]
            sents = sum(len(b["sentences"]) for b in source_paras(src["blocks"] + src.get("appendix", [])))
            paras_total = len(ids)
        rev = review_count(book, cid)
        lab_f, pat_f = book.root / "labels" / f"{cid}.yaml", book.patch_dir / f"{cid}.json"
        labels = len(yaml.safe_load(lab_f.read_text(encoding="utf-8")) or {}) if lab_f.exists() else 0
        patches = len(json.loads(pat_f.read_text(encoding="utf-8"))) if pat_f.exists() else 0
        ann_dir = book.ann_dir / cid
        done = density = None
        if ann_dir.is_dir() and row["extracted"]:
            have = set()
            for f in ann_dir.glob("paras*.json"):
                try:
                    have |= set(json.loads(f.read_text(encoding="utf-8")))
                except json.JSONDecodeError:
                    pass
            row["missing"] = [i for i in ids if i not in have]
            done = paras_total - len(row["missing"])
            row["annotated"] = True
            problems, st = checker.check(n)
            row["check"] = problems
            density = st.get("density") if st else None
        rows[n] = row
        chk = "-" if row["check"] is None else "ok" if not row["check"] else f"{len(row['check'])} problems"
        print(f"{n:3} {sents if row['extracted'] else '-':>9} {'-' if rev is None else rev:>6} {labels or '-':>6}"
              f" {patches or '-':>7} {f'{done}/{paras_total}' if done is not None else '-':>11}"
              f" {f'{density:.1f}' if density is not None else '-':>9}  {chk:10} {noted.get(n, '')}")
    if ann.get("notes_per_100"):
        print(f"\ntarget {ann['notes_per_100']} notes per 100 sentences (check.py fails below {checker.minimum:.1f})")

    # next step: the first chapter the user has not approved
    pending = [n for n in nums if not noted.get(n, "").lower().startswith("approved")]
    print()
    if not pending:
        print("next: every chapter is approved. Run quality.py; export_epub.py if the EPUB is out of date.")
    else:
        n = pending[0]
        r, cid = rows[n], f"ch{n:02d}"
        state = noted.get(n, "").lower()
        if not r["extracted"]:
            step = (f"extract.py {n}; then overlay.py {n} --review and read build/source/{cid}.txt; "
                    f"fix structure with labels/{cid}.yaml and text with patches/{cid}.json")
        elif not r["annotated"]:
            step = (f"review the extraction (overlay.py {n} --review, build/source/{cid}.txt), finish labels and "
                    f"patches, re-extract, then write annotations/{cid}/ against the final paragraph ids")
        elif r["missing"]:
            step = (f"annotate the remaining {len(r['missing'])} paragraphs (first missing: {r['missing'][0]}), "
                    f"then build.py {n} and check.py {n}")
        elif r["check"]:
            step = f"fix the {len(r['check'])} problems check.py {n} reports, then build.py {n} and check.py {n} again"
        elif "review" in state or "await" in state:
            step = "waiting for the user to review and approve it; do not start the next chapter before that"
        else:
            step = (f"open the reader at #{cid}, update NOTES.md (status, outline, hand-over), "
                    "then hand the chapter to the user for approval")
        print(f"next: chapter {n}: {step}")
        print("Read NOTES.md (hand-over notes) before starting.")

    epub = book.export_dir / f"{book.cfg['book']['epub_name']}.epub"
    data = list(book.data_dir.glob("ch*.js"))
    if data:
        if not epub.exists():
            print("EPUB: not exported yet (export_epub.py)")
        elif epub.stat().st_mtime < max(f.stat().st_mtime for f in data):
            print("EPUB: older than the reader data; run export_epub.py and check_epub.py when the chapter is approved")
        else:
            print("EPUB: up to date")


if __name__ == "__main__":
    main()
