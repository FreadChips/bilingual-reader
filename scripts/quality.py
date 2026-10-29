"""Whole-book checks on the annotations, to run before calling a book finished.

Usage: python quality.py [--book DIR]

Reports
  - note density per chapter (subject notes, language notes, paragraph summaries per 100 sentences),
    flagging chapters far below the book's median: the standard tends to drift as chapters go by
  - glossary terms translated differently in different chapters
  - leftover markup in translations (HTML tags, &lt; entities, "undefined")
  - "第N章" references to chapters the book does not have
"""
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I, which leaves out the script's folder
from bookkit import open_book  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    book, _ = open_book(sys.argv[1:])
    chapters = sorted(p for p in book.ann_dir.glob("ch*") if p.is_dir())
    nums = {int(re.sub(r"\D", "", p.name)) for p in chapters} | \
           {int(n) for n in book.cfg["chapters"].get("titles_zh") or {}}
    rows, gloss, problems = [], defaultdict(set), []
    for d in chapters:
        n_sent = n_note = n_lang = n_sum = 0
        for f in sorted(d.glob("paras-*.json")):
            for pid, p in json.loads(f.read_text(encoding="utf-8")).items():
                n_sum += bool(p.get("sum"))
                for s in p.get("s", []):
                    n_sent += 1
                    if isinstance(s, dict):
                        n_note += bool(s.get("n"))
                        n_lang += bool(s.get("l"))
        meta = d / "meta.json"
        if meta.exists():
            m = json.loads(meta.read_text(encoding="utf-8"))
            for term, g in (m.get("glossary") or {}).items():
                gloss[term].add((g.get("zh", ""), d.name))
        for f in sorted(d.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            data.pop("math", None)  # translated equations are HTML on purpose
            text = json.dumps(data, ensure_ascii=False)
            for bad in sorted(set(re.findall(r"</?(?:i|b|span|div|sub|sup)>|&lt;|&gt;|&amp;|undefined", text))):
                problems.append(f"{f.parent.name}/{f.name}: leftover {bad!r}")
            for ref in sorted({int(x) for x in re.findall(r"第(\d+)章", text)} - nums):
                problems.append(f"{f.parent.name}/{f.name}: refers to 第{ref}章, which the book does not have")
        if n_sent:
            rows.append((d.name, n_sent, n_note, n_lang, n_sum))

    if rows:
        dens = [100 * r[2] / r[1] for r in rows]
        med = statistics.median(dens)
        print("chapter  sentences  notes  lang  summaries  notes/100 sentences")
        for r, x in zip(rows, dens):
            flag = "   ← far below the median" if x < 0.6 * med else ""
            print(f"{r[0]:7}  {r[1]:9}  {r[2]:5}  {r[3]:4}  {r[4]:9}  {x:6.1f}{flag}")
        print(f"median: {med:.1f} notes per 100 sentences")
    for term, zh in sorted(gloss.items()):
        if len({z for z, _ in zh}) > 1:
            problems.append(f"glossary '{term}' translated differently: {sorted(zh)}")
    print("\n" + ("\n".join(problems) if problems else "no other problems found"))


if __name__ == "__main__":
    main()
