"""Steps every extracted chapter goes through, whatever the source format.

ChapterBase holds the parsed blocks of one chapter and provides hand patches, paragraph
numbering, figure placement, the JSON for build.py and the .txt dump for review.
Subclasses (extract_pdf.PdfChapter, extract_epub.EpubChapter) fill self.blocks, self.appendix,
self.glossary, self.photos, self.endmatter and self.pages in run().
"""
import json
import re
import sys
import traceback
import unicodedata
from collections import Counter


def extract_each(nums, extract_one):
    """Run extract_one(num) for every chapter. A chapter that fails is reported and the rest still run,
    so one crash cannot hide the chapters after it; the run then exits non-zero naming the failures."""
    failed = []
    for num in nums:
        try:
            extract_one(num)
        except SystemExit as e:  # a deliberate stop: a label naming no region, a patch that matches nothing
            print(f"ch{num:02d} FAILED: {e}", file=sys.stderr)
            failed.append(num)
        except Exception:
            print(f"ch{num:02d} FAILED:", file=sys.stderr)
            traceback.print_exc()
            failed.append(num)
    if failed:
        raise SystemExit(f"{len(failed)} chapter(s) failed: {', '.join(f'ch{n:02d}' for n in failed)}")


class ChapterBase:
    def __init__(self, book, num, title):
        self.book = book
        self.num, self.title = num, title
        self.cid = f"ch{num:02d}"
        self.glossary, self.photos = [], []
        self.blocks, self.appendix = [], []
        self.endmatter = {}
        self.pages = [None, None]
        self.counters = Counter()

    def new_id(self, prefix):
        self.counters[prefix] += 1
        return f"{prefix}{self.counters[prefix]:02d}"

    # ------------------------------------------------------------ hand patches
    def apply_patches(self):
        """Hand fixes for text the source encodes badly. See patches/chNN.json and references/patches.md."""
        path = self.book.patch_dir / f"{self.cid}.json"
        if not path.exists():
            return
        sent = lambda html: {"id": "", "html": html}
        # matching ignores sub/superscript tags, so a patch keeps working when the engine marks them,
        # and composed vs combining accents ("Ḡ" / "G" + U+0304)
        flat = lambda html: unicodedata.normalize("NFC", re.sub(r"</?su[bp]>", "", html))
        for patch in json.loads(path.read_text(encoding="utf-8")):
            if "figure" in patch:  # a figure's or table's title, caption or source read wrongly
                fig = next((x for l in self.block_lists(self.blocks + self.appendix, [self.blocks, self.appendix])
                            for x in l if x["type"] in ("figure", "table") and x["id"] == patch["figure"]), None)
                if not fig:
                    raise SystemExit(f"patch: no figure or table {patch['figure']}")
                fig.update({k: patch[k] for k in ("title", "caption", "source") if k in patch})
                continue
            if "math_match" in patch:  # a displayed equation the source garbles: rewrite or drop it
                eqs = [(l, k) for l in self.block_lists(self.blocks + self.appendix, [self.blocks, self.appendix])
                       for k, x in enumerate(l) if x["type"] == "math"]
                frag = patch["math_match"]  # an equation equal to the fragment wins over one containing it
                hit = next(((l, k) for l, k in eqs if flat(l[k]["html"]).strip() == flat(frag).strip()), None) \
                    or next(((l, k) for l, k in eqs if flat(frag) in flat(l[k]["html"])), None)
                if not hit:
                    raise SystemExit(f"patch did not match an equation: {patch['math_match']}")
                if patch.get("delete"):
                    del hit[0][hit[1]]
                else:
                    hit[0][hit[1]]["html"] = patch["math"]
                continue
            hit = None
            for lst in self.block_lists(self.blocks + self.appendix, [self.blocks, self.appendix]):
                for i, b in enumerate(lst):
                    if b.get("sentences") and flat(patch["match"]) in flat(" ".join(s["html"] for s in b["sentences"])):
                        hit = (lst, i, b)
                        break
                if hit:
                    break
            if not hit:
                raise SystemExit(f"patch did not match: {patch['match']}")
            lst, i, b = hit
            if "box" in patch:  # repair the enclosing box's own fields (a title read as body text)
                owner = next(x for l in self.block_lists(self.blocks + self.appendix, [self.blocks, self.appendix])
                             for x in l if x["type"] == "box" and x["blocks"] is lst)
                owner.update(patch["box"])
            if "move_after" in patch:  # two boxes spilling onto one page get interleaved
                del lst[i]
                dest = next((l, k) for l in self.block_lists(self.blocks + self.appendix, [self.blocks, self.appendix])
                            for k, x in enumerate(l) if x.get("sentences")
                            and flat(patch["move_after"]) in flat(" ".join(s["html"] for s in x["sentences"])))
                dest[0].insert(dest[1] + 1, b)
                continue
            if patch.get("delete"):
                del lst[i]
                continue
            for old, new in patch.get("fix", []):  # typos in the source ("be fore", a run-in drop cap)
                for s in b["sentences"]:
                    if old not in s["html"] and old in flat(s["html"]):
                        s["html"] = flat(s["html"])
                    s["html"] = s["html"].replace(old, new)
            if "type" in patch:  # e.g. a run-in list set as one paragraph -> bullets
                b["type"] = patch["type"]
            if "parts" in patch:  # rebuild the paragraph as a run of text and displayed equations
                new = []
                for part in patch["parts"]:
                    if "math" in part:
                        new.append({"type": "math", "id": self.new_id("eq"), "page": b["page"], "html": part["math"]})
                    else:
                        new.append({**b, "sentences": [sent(h) for h in part["s"]],
                                    "type": part.get("type", b["type"]),  # e.g. a law quoted mid-paragraph
                                    "terms": b["terms"] if not new else []})
                lst[i:i + 1] = new
                continue
            if "sentences" in patch:
                b["sentences"] = [sent(h) for h in patch["sentences"]]
            if "replace_first" in patch:
                b["sentences"][0] = sent(patch["replace_first"])
            if "replace_last" in patch:
                b["sentences"][-1] = sent(patch["replace_last"])
            if "math" in patch:
                lst[i] = {"type": "math", "id": self.new_id("eq"), "page": b["page"], "html": patch["math"]}
            if "then_math" in patch:
                extra = [{"type": "math", "id": self.new_id("eq"), "page": b["page"], "html": patch["then_math"]}]
                if patch.get("then_sentences"):
                    extra.append({**b, "sentences": [sent(h) for h in patch["then_sentences"]], "terms": []})
                lst[i + 1:i + 1] = extra

    # ------------------------------------------------------------ structure helpers
    def block_lists(self, blocks, acc):
        for b in blocks:
            if b["type"] == "box":
                acc.append(b["blocks"])
                self.block_lists(b["blocks"], acc)
        return acc

    def renumber(self):
        """Paragraph ids follow reading order (boxes are parsed before the paragraph they interrupt)."""
        n = 0
        for p in self.iter_paras(self.blocks + self.appendix):
            n += 1
            p["id"] = f"p{n:02d}"
            for k, s in enumerate(p["sentences"]):
                s["id"] = f"{p['id']}.{k + 1}"

    def place_figures(self):
        """Move each figure/table to just after the paragraph that first mentions it ("Figure 3",
        "Table A-1"), which may be inside a box; on the page it often sits several paragraphs later."""
        def walk(lst):
            for b in lst:
                yield lst, b
                if b["type"] == "box":
                    yield from walk(b["blocks"])
        order = list(walk(self.blocks)) + list(walk(self.appendix))
        moves = []
        for lst, b in order:
            if b["type"] not in ("figure", "table"):
                continue
            pat = re.compile(r"\b" + re.escape(b["label"]).replace(r"\ ", r"\s") + r"(?![\d-])")  # "Figure\xa01"
            hit = next((p for _, p in order if p.get("sentences")
                        and pat.search(" ".join(s["html"] for s in p["sentences"]))), None)
            if hit:
                moves.append((lst, b, hit))
        for lst, b, _ in moves:
            lst.remove(b)
        for _, b, para in moves:  # in original order, so figures after one paragraph keep their sequence
            lst = next(l for top in (self.blocks, self.appendix) for l, p in walk(top) if p is para)
            i = next(k for k, p in enumerate(lst) if p is para) + 1
            while i < len(lst) and lst[i]["type"] in ("figure", "table"):
                i += 1
            lst.insert(i, b)

    def iter_paras(self, blocks=None):
        for b in blocks if blocks is not None else self.blocks + self.appendix:
            if b["type"] in ("para", "quote", "bullets"):
                yield b
            elif b["type"] == "box":
                yield from self.iter_paras(b["blocks"])

    # ------------------------------------------------------------ output
    def to_json(self):
        def clean(blocks):
            for b in blocks:
                b.pop("y", None)
                if b["type"] == "box":
                    clean(b["blocks"])
            return blocks
        return {
            "id": self.cid, "number": self.num, "title": self.title,
            "pages": self.pages,
            "blocks": clean(self.blocks), "appendix": clean(self.appendix),
            "glossary": self.glossary, "photos": self.photos, "end": self.endmatter,
        }

    def write(self):
        data = self.to_json()
        out = self.book.src_dir
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{data['id']}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"{data['id']}.txt").write_text(dump_txt(data), encoding="utf-8")
        paras = list(self.iter_paras())
        n_sent = sum(len(p["sentences"]) for p in paras)
        n_fig = sum(1 for x in self.block_lists(self.blocks + self.appendix, [self.blocks, self.appendix])
                    for y in x if y["type"] in ("figure", "table"))
        print(f"{data['id']}: {len(paras)} paragraphs, {n_sent} sentences, {len(data['glossary'])} terms, "
              f"{len(data['photos'])} photos, {n_fig} figures/tables, appendix blocks: {len(data['appendix'])}")
        return data


def dump_txt(data):
    out = [f"# {data['id']} {data['title']}"]

    def walk(blocks, indent=""):
        for b in blocks:
            t = b["type"]
            if t == "heading":
                out.append(f"\n{indent}{'##' if b['level'] == 1 else '###'} [{b['id']}] {b['num']} {b['text']}")
            elif t in ("para", "quote", "bullets"):
                out.append(f"\n{indent}[{b['id']}] ({t}, p.{b['page']}) terms={b['terms']}")
                for s in b["sentences"]:
                    out.append(f"{indent}  {s['id']}: {s['html']}")
            elif t == "box":
                out.append(f"\n{indent}=== BOX [{b['id']}] {b['label']} :: {b['title']}")
                for k in ("intro", "headline", "byline", "source", "author"):
                    if b.get(k):
                        out.append(f"{indent}    ({k}) {b[k]}")
                walk(b["blocks"], indent + "    ")
                out.append(f"{indent}=== END BOX")
            elif t == "quickquiz":
                out.append(f"\n{indent}[{b['id']}] QUICK QUIZ")
                for it in b["items"]:
                    out.append(f"{indent}  {it['id']}: {it['html']}")
            elif t in ("table", "figure"):
                out.append(f"\n{indent}[{b['id']}] {b['label']}: {b.get('title', '')} -> {b['src']}")
                if b.get("caption"):
                    out.append(f"{indent}  caption: {b['caption']}")
                for k, r in enumerate(b.get("rows", [])):
                    out.append(f"{indent}  row{k + 1}: {r}")
                if b.get("grid"):
                    g = b["grid"]
                    out.append(f"{indent}  grid: {len(g['rows'])} rows, header {g['header']}")
                    for k, row in enumerate(g["rows"]):
                        cells = [c if isinstance(c, str) else f"{c['t']} <span {c['span']}>" for c in row]
                        out.append(f"{indent}  {'H' if k < g['header'] else ' '}| " + " | ".join(cells))
                if b.get("source"):
                    out.append(f"{indent}  source: {b['source']}")
            elif t == "math":
                out.append(f"\n{indent}[{b['id']}] MATH: {b['html']}")
            elif t == "experts":
                out.append(f"\n{indent}[{b['id']}] ASK THE EXPERTS: {b['title']}")
                for it in b["items"]:
                    out.append(f"{indent}  proposition: {it['proposition']}  results: {it['results']}")
                out.append(f"{indent}  source: {b['source']}")
    walk(data["blocks"])
    if data["appendix"]:
        out.append("\n# APPENDIX")
        walk(data["appendix"])
    out.append("\n# GLOSSARY")
    for g in data["glossary"]:
        out.append(f"  {g['term']} :: {g['def']} (p.{g['page']}, para {g['para']})")
    out.append("\n# PHOTOS")
    for p in data["photos"]:
        out.append(f"  [{p['id']}] {p['caption']} -> {p['src']} (para {p['para']})")
    out.append("\n# END MATTER")
    for q in data["end"].get("quiz", []):
        out.append(f"  {q['id']}: {q['stem']}")
        for k, v in q["options"].items():
            out.append(f"      {k}. {v}")
    for s in data["end"].get("summary", []):
        out.append(f"  {s['id']}: {s['html']}")
    return "\n".join(out)
