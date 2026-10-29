"""Merge extracted source text with annotations into reader data files.

Usage: python build.py [--book DIR]            (all annotated chapters)
       python build.py [--book DIR] 1 2       (some chapters)

Reads   build/source/chNN.json and annotations/chNN/*.json
Writes  reader/data/chNN.js and reader/data/book.js (book title and chapter list from book.yaml)
Exits non-zero when a translation is missing or sentence counts disagree.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I, which leaves out the script's folder
from bookkit import open_book  # noqa: E402
from tablekit import needs_translation  # noqa: E402

BOOK = SRC = ANN = OUT = None  # set in main()


class Problems(list):
    def add(self, msg):
        self.append(msg)
        print("  !", msg)


def load_annotations(cid):
    merged = {"paras": {}}
    folder = ANN / cid
    for f in sorted(folder.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{f}: {e}")
        if f.stem.startswith("paras"):
            dup = set(data) & set(merged["paras"])
            if dup:
                raise SystemExit(f"{f}: duplicate paragraphs {sorted(dup)}")
            merged["paras"].update(data)
        else:
            merged.update(data)
    return merged


def sentence_entry(src, ann):
    if isinstance(ann, str):
        ann = {"t": ann}
    out = {"id": src["id"], "en": src["html"], "zh": ann.get("t", "")}
    if ann.get("n"):
        out["n"] = ann["n"]
    if ann.get("l"):
        out["l"] = ann["l"]
    return out


def grid_entry(b, cells, problems):
    """A table's cells with their translations (meta.json tables.ID.cells: {English cell: 中文})."""
    rows, missing = [], []
    for row in b["grid"]["rows"]:
        out = []
        for c in row:
            text, span = (c, 1) if isinstance(c, str) else (c["t"], c["span"])
            zh = cells.get(text, "")
            if needs_translation(text) and not zh:
                missing.append(text)
            out.append({"en": text, "zh": zh, "span": span})
        rows.append(out)
    if missing:
        problems.add(f"{b['id']}: {len(missing)} table cells without translation in meta.json cells, "
                     f"e.g. {missing[0]!r}")
    return {"header": b["grid"]["header"], "rows": rows}


def build_chapter(num):
    cid = f"ch{num:02d}"
    src = json.loads((SRC / f"{cid}.json").read_text(encoding="utf-8"))
    ann = load_annotations(cid)
    problems = Problems()
    gloss_ann = ann.get("glossary", {})

    glossary = []
    for g in src["glossary"]:
        a = gloss_ann.get(g["term"].lower(), {})
        if not a:
            problems.add(f"glossary term without translation: {g['term']}")
        glossary.append({"term": g["term"], "def": g["def"], "zh": a.get("zh", ""),
                         "def_zh": a.get("def", ""), "page": g["page"], "para": g["para"]})
    terms_by_para, photos_by_para = {}, {}
    for g in glossary:
        terms_by_para.setdefault(g["para"], []).append(g)
    for p in src["photos"]:
        a = ann.get("photos", {}).get(p["id"], {})
        photos_by_para.setdefault(p["para"], []).append(
            {"id": p["id"], "src": p["src"], "caption": p["caption"], "zh": a.get("zh", ""), "n": a.get("n", "")})

    seen = set()

    def convert(blocks):
        out = []
        for b in blocks:
            t = b["type"]
            if t == "heading":
                zh = ann.get("headings", {}).get(b["id"], "")
                if not zh:
                    problems.add(f"heading without translation: {b['id']}")
                out.append({**b, "zh": zh})
            elif t in ("para", "quote", "bullets"):
                a = ann["paras"].get(b["id"])
                seen.add(b["id"])
                if a is None:
                    problems.add(f"{b['id']}: no annotation")
                    a = {"s": []}
                if len(a["s"]) != len(b["sentences"]):
                    problems.add(f"{b['id']}: {len(b['sentences'])} sentences in source, "
                                 f"{len(a['s'])} in annotation")
                sents = [sentence_entry(s, a["s"][i] if i < len(a["s"]) else "")
                         for i, s in enumerate(b["sentences"])]
                out.append({"type": t, "id": b["id"], "page": b["page"], "sentences": sents,
                            "sum": a.get("sum", ""), "terms": terms_by_para.get(b["id"], []),
                            "photos": photos_by_para.get(b["id"], [])})
            elif t == "box":
                a = ann.get("boxes", {}).get(b["id"], {})
                box = {"type": "box", "id": b["id"], "label": b["label"], "title": b["title"],
                       "label_zh": a.get("label", ""), "title_zh": a.get("title", ""),
                       "page": b["page"], "blocks": convert(b["blocks"])}
                for k in ("intro", "headline", "byline", "source", "author"):
                    if b.get(k):
                        box[k] = b[k]
                        box[k + "_zh"] = a.get(k, "")
                        if k in ("intro", "headline") and not a.get(k):
                            problems.add(f"{b['id']}: {k} without translation")
                out.append(box)
            elif t == "experts":
                a = ann.get("experts", {}).get(b["id"], {})
                zh = a.get("propositions") or ([a["proposition"]] if a.get("proposition") else [])
                if len(zh) != len(b["items"]):
                    problems.add(f"{b['id']}: {len(b['items'])} propositions, {len(zh)} translated")
                items = [{**it, "zh": zh[i] if i < len(zh) else ""} for i, it in enumerate(b["items"])]
                out.append({**b, "items": items, "title_zh": a.get("title", ""), "n": a.get("n", "")})
            elif t == "math":
                out.append({**b, "zh": ann.get("math", {}).get(b["id"], "")})
            elif t == "quickquiz":
                a = ann.get("quickquiz", {}).get(b["id"], [])
                if len(a) != len(b["items"]):
                    problems.add(f"{b['id']}: {len(b['items'])} quiz items, {len(a)} translated")
                items = [{"en": it["html"], "zh": (a[i] if i < len(a) else {}).get("zh", ""),
                          "hint": (a[i] if i < len(a) else {}).get("hint", "")}
                         for i, it in enumerate(b["items"])]
                out.append({"type": "quickquiz", "id": b["id"], "page": b["page"], "items": items})
            elif t in ("table", "figure"):
                a = ann.get("figures", {}).get(b["id"]) or ann.get("tables", {}).get(b["id"], {})
                if b.get("title") and not a.get("title"):
                    problems.add(f"{b['id']}: title without translation")
                if b.get("caption") and not a.get("caption"):
                    problems.add(f"{b['id']}: caption without translation")
                fig = {"type": t, "id": b["id"], "label": b["label"], "title": b.get("title", ""),
                       "title_zh": a.get("title", ""), "n": a.get("n", ""), "src": b.get("src", ""), "page": b["page"],
                       "caption": b.get("caption", ""), "caption_zh": a.get("caption", ""),
                       "source": b.get("source", "")}
                if b.get("rows"):
                    rz = a.get("rows", [])
                    if len(rz) != len(b["rows"]):
                        problems.add(f"{b['id']}: {len(b['rows'])} rows, {len(rz)} translated")
                    fig["rows"] = [{"en": r, "zh": rz[i] if i < len(rz) else ""} for i, r in enumerate(b["rows"])]
                if b.get("grid"):
                    fig["grid"] = grid_entry(b, a.get("cells", {}), problems)
                out.append(fig)
        return out

    blocks = convert(src["blocks"])
    appendix = convert(src.get("appendix", []))
    extra = set(ann["paras"]) - seen
    if extra:
        problems.add(f"annotations for unknown paragraphs: {sorted(extra)}")

    end = {}
    qa = ann.get("end", {}).get("quiz", {})
    if src["end"].get("quiz"):
        end["quiz"] = []
        for q in src["end"]["quiz"]:
            a = qa.get(q["id"], {})
            if not a:
                problems.add(f"{q['id']}: no translation")
            end["quiz"].append({
                "id": q["id"], "stem": q["stem"], "stem_zh": a.get("stem", ""),
                "options": [{"key": k, "en": v, "zh": a.get("options", {}).get(k, "")}
                            for k, v in q["options"].items()],
                "answer": a.get("answer", ""), "explain": a.get("explain", ""),
            })
    sa = ann.get("end", {}).get("summary", {})
    if src["end"].get("summary"):
        end["summary"] = [{"en": s["html"], "zh": sa.get(s["id"], "")} for s in src["end"]["summary"]]

    def paragraphs(v):  # a guide is a list of paragraphs; a single string is split at blank lines
        return [p.strip() for p in v.split("\n\n") if p.strip()] if isinstance(v, str) else v

    data = {
        "id": cid, "number": num, "title": src["title"], "title_zh": ann.get("title_zh", BOOK.title_zh(num)),
        "pages": src["pages"], "guide": paragraphs(ann.get("guide", [])), "blocks": blocks,
        "appendix": appendix, "appendix_guide": paragraphs(ann.get("appendix_guide", [])),
        "glossary": glossary, "end": end,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    js = (f"window.BOOK_DATA = window.BOOK_DATA || {{}};\n"
          f"window.BOOK_DATA[{json.dumps(cid)}] = {json.dumps(data, ensure_ascii=False)};\n")
    (OUT / f"{cid}.js").write_text(js, encoding="utf-8")
    n_sent = sum(len(p["sentences"]) for p in iter_paras(blocks + appendix))
    n_notes = sum(1 for p in iter_paras(blocks + appendix) for s in p["sentences"] if s.get("n"))
    n_lang = sum(1 for p in iter_paras(blocks + appendix) for s in p["sentences"] if s.get("l"))
    print(f"{cid}: {n_sent} sentences, {n_notes} notes, {n_lang} language notes, "
          f"{len(glossary)} terms, {len(problems)} problems")
    return not problems


def iter_paras(blocks):
    for b in blocks:
        if b["type"] in ("para", "quote", "bullets"):
            yield b
        elif b["type"] == "box":
            yield from iter_paras(b["blocks"])


def write_index():
    """reader/data/book.js: book fields for the reader, and every chapter known from the profile or the source."""
    src_titles = {}
    for f in SRC.glob("ch*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        src_titles[d["number"]] = d["title"]
    nums = sorted({int(n) for n in BOOK.cfg["chapters"].get("titles_zh") or {}} | set(src_titles))
    include = BOOK.cfg["chapters"].get("include")  # the chapters agreed to be done (a filing's Items)
    if include:
        nums = [n for n in nums if n in set(include)]
    chapters = []
    for num in nums:
        cid = f"ch{num:02d}"
        chapters.append({"id": cid, "number": num, "title": src_titles.get(num, ""), "title_zh": BOOK.title_zh(num),
                         "available": (OUT / f"{cid}.js").exists()})
    b = BOOK.cfg["book"]
    index = {"kind": b["kind"], "title": b["title"], "title_zh": b["title_zh"], "author": b["author"], "subject": b["subject"],
             "note_label": f'{b["subject"]}{BOOK.cfg["notes"]["label"]}', "source_note": b["source_note"],
             "storage_key": b.get("storage_key") or b["epub_name"],
             "chapters": chapters}
    (OUT / "book.js").write_text(f"window.BOOK_INDEX = {json.dumps(index, ensure_ascii=False, indent=1)};\n",
                                 encoding="utf-8")


def main():
    global BOOK, SRC, ANN, OUT
    sys.stdout.reconfigure(encoding="utf-8")
    BOOK, args = open_book(sys.argv[1:])
    SRC, ANN, OUT = BOOK.src_dir, BOOK.ann_dir, BOOK.data_dir
    if args:
        nums = [int(a) for a in args]
    else:
        nums = sorted(int(re.sub(r"\D", "", p.name)) for p in ANN.glob("ch*") if p.is_dir())
    ok = all([build_chapter(n) for n in nums])
    write_index()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
