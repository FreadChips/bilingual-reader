"""Checks a chapter must pass before it is handed to the user. Needs no browser.

Usage: python check.py [--book DIR] 3          one chapter
       python check.py [--book DIR] 3 4 5      several
       python check.py [--book DIR] all        every annotated chapter

Run after build.py. Checks
  - annotation files: JSON shape (paras-*.json, meta.json), every paragraph and heading of the source
    annotated, one translation per source sentence;
  - writing: a translation in Chinese for every real sentence, no single-asterisk italics, no
    unbalanced **bold**, no HTML or entities outside `math`, no "undefined", no 第N章 the book lacks;
  - note density against book.yaml annotation.notes_per_100 (fails below annotation.notes_min,
    default 85% of the target);
  - reader data (reader/data/chNN.js): built from the current source and annotations (compared by
    content, so a re-extraction that changed nothing does not count), parses, has the shape the
    reader page expects, every image it names exists.
Exit status 1 when any chapter has a problem. build.py remains the full alignment check.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I, which leaves out the script's folder
from bookkit import open_book  # noqa: E402
from tablekit import needs_translation  # noqa: E402

PARA_KEYS = {"s", "sum"}
SENT_KEYS = {"t", "n", "l"}
META_KEYS = {"title_zh", "guide", "headings", "boxes", "figures", "tables", "photos", "glossary", "quickquiz",
             "experts", "math", "end", "appendix_guide"}
BLOCK_TYPES = {"heading", "para", "quote", "bullets", "box", "experts", "math", "quickquiz", "table", "figure"}
CJK = re.compile(r"[一-鿿]")
# single-asterisk italics around Chinese text; stars used as symbols (k*, r₁*) are left alone (as strip_italics.py)
ITALIC = re.compile(r'(?<![A-Za-z0-9À-ɏͰ-Ͽ̀-ͯ⁰-₟)\]*′])\*(?![*\s=])([^*\n"]*?[一-鿿][^*\n"]*?)(?<![\s*])\*(?![A-Za-z0-9À-ɏͰ-Ͽ⁰-₟*])')
MARKUP = re.compile(r"</?(?:i|b|em|strong|span|div|sub|sup|br|p)\b[^>]*>|&lt;|&gt;|&amp;|&nbsp;")


def strip_tags(html):
    return re.sub(r"<[^>]+>", "", html)


def source_paras(blocks):
    for b in blocks:
        if b["type"] in ("para", "quote", "bullets"):
            yield b
        elif b["type"] == "box":
            yield from source_paras(b["blocks"])


def source_blocks(blocks):
    for b in blocks:
        yield b
        if b["type"] == "box":
            yield from source_blocks(b["blocks"])


def strings(x, path=""):
    """Every string in an annotation structure, with its path."""
    if isinstance(x, str):
        yield path, x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from strings(v, f"{path}.{k}" if path else k)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from strings(v, f"{path}[{i}]")


class Checker:
    def __init__(self, book):
        self.book = book
        ann = book.cfg.get("annotation") or {}
        self.target = ann.get("notes_per_100")
        self.minimum = ann.get("notes_min") or (0.85 * self.target if self.target else None)
        self.chapters = ({int(re.sub(r"\D", "", p.stem)) for p in book.src_dir.glob("ch*.json")}
                         | {int(re.sub(r"\D", "", p.name)) for p in book.ann_dir.glob("ch*") if p.is_dir()}
                         | {int(n) for n in book.cfg["chapters"].get("titles_zh") or {}})

    def check(self, num):
        """Returns (problems, stats)."""
        cid = f"ch{num:02d}"
        problems = []
        add = problems.append
        src_f = self.book.src_dir / f"{cid}.json"
        ann_dir = self.book.ann_dir / cid
        if not src_f.exists():
            return [f"no build/source/{cid}.json: run extract.py {num}"], {}
        if not ann_dir.is_dir():
            return [f"no annotations/{cid}/"], {}
        src = json.loads(src_f.read_text(encoding="utf-8"))

        # --- annotation files
        paras, meta, files = {}, {}, sorted(ann_dir.glob("*.json"))
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                add(f"{f.name}: not valid JSON ({e})")
                continue
            if not isinstance(data, dict):
                add(f"{f.name}: must be a JSON object")
                continue
            if f.stem.startswith("paras"):
                for pid in set(data) & set(paras):
                    add(f"{f.name}: {pid} also in another paras file")
                paras.update({k: (v, f.name) for k, v in data.items()})
            elif f.name == "meta.json":
                meta = data
            else:
                add(f"{f.name}: unexpected file (only meta.json and paras-*.json are read)")
        if "meta.json" not in {f.name for f in files}:
            add("meta.json missing")
        for k in set(meta) - META_KEYS:
            add(f"meta.json: unknown key {k!r}")
        if not isinstance(meta.get("title_zh", ""), str) or not meta.get("title_zh"):
            add("meta.json: title_zh missing")
        for key in ("guide", "appendix_guide"):
            g = meta.get(key)
            if g is not None and not (isinstance(g, str) or (isinstance(g, list) and all(isinstance(p, str) for p in g))):
                add(f"meta.json: {key} must be a list of paragraph strings")
        for term in meta.get("glossary") or {}:
            if term != term.lower():
                add(f"meta.json: glossary key {term!r} must be lower case")

        # --- paragraphs and sentences
        n_sent = n_note = n_lang = 0
        blocks = src["blocks"] + src.get("appendix", [])
        seen = set()
        for b in source_paras(blocks):
            seen.add(b["id"])
            if b["id"] not in paras:
                add(f"{b['id']}: no annotation")
                continue
            p, fname = paras[b["id"]]
            if not isinstance(p, dict) or not isinstance(p.get("s"), list):
                add(f"{fname} {b['id']}: must be {{\"s\": [...]}}")
                continue
            for k in set(p) - PARA_KEYS:
                add(f"{fname} {b['id']}: unknown key {k!r}")
            if len(p["s"]) != len(b["sentences"]):
                add(f"{fname} {b['id']}: {len(b['sentences'])} sentences in source, {len(p['s'])} in annotation")
            for i, (s, en) in enumerate(zip(p["s"], b["sentences"])):
                where = f"{fname} {b['id']}.{i + 1}"
                if isinstance(s, str):
                    s = {"t": s}
                if not isinstance(s, dict):
                    add(f"{where}: must be a string or an object")
                    continue
                for k in set(s) - SENT_KEYS:
                    add(f"{where}: unknown key {k!r}")
                t = s.get("t")
                if not isinstance(t, str) or not t.strip():
                    add(f"{where}: no translation")
                    continue
                en_text = strip_tags(en["html"])
                if len(re.findall(r"[A-Za-z]{2,}", en_text)) >= 4 and not CJK.search(t):
                    add(f"{where}: translation has no Chinese: {t[:40]!r}")
                for k in ("n", "l"):
                    if k in s and (not isinstance(s[k], str) or not s[k].strip()):
                        add(f"{where}: empty {k!r}")
                n_sent += 1
                n_note += bool(s.get("n"))
                n_lang += bool(s.get("l"))
        for pid in sorted(set(paras) - seen):
            add(f"{paras[pid][1]} {pid}: not a paragraph of the source (ids shifted?)")
        heads = meta.get("headings") or {}
        for b in source_blocks(blocks):
            if b["type"] == "heading" and not heads.get(b["id"]):
                add(f"heading {b['id']}: no translation in meta.json headings")
            if b["type"] in ("figure", "table"):
                a = (meta.get("figures") or {}).get(b["id"]) or (meta.get("tables") or {}).get(b["id"]) or {}
                if b.get("title") and not a.get("title"):
                    add(f"{b['id']}: no translated title in meta.json figures/tables")
                if b.get("grid"):
                    texts = {c if isinstance(c, str) else c["t"] for row in b["grid"]["rows"] for c in row}
                    cells = a.get("cells") or {}
                    for c in sorted(t for t in texts if needs_translation(t) and not cells.get(t)):
                        add(f"{b['id']}: table cell {c!r} has no translation in meta.json cells")
                    for c in sorted(set(cells) - texts):
                        add(f"{b['id']}: meta.json cells has {c!r}, which is not a cell of the table")

        # --- writing
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if f.name == "meta.json":
                data = {k: v for k, v in data.items() if k != "math"}  # translated equations are HTML on purpose
            for path, text in strings(data):
                where = f"{f.name} {path}"
                for m in ITALIC.finditer(text):
                    add(f"{where}: single-asterisk italics {m.group(0)[:30]!r} (only **bold** renders; strip_italics.py)")
                if text.count("**") % 2:
                    add(f"{where}: unbalanced ** bold marker")
                for m in sorted(set(MARKUP.findall(text))):
                    add(f"{where}: markup {m!r} (write plain text, Unicode sub/superscripts)")
                if "undefined" in text:
                    add(f"{where}: contains 'undefined'")
                for ref in sorted({int(x) for x in re.findall(r"第(\d+)章", text)} - self.chapters):
                    add(f"{where}: refers to 第{ref}章, which the book does not have")

        # --- density
        density = 100 * n_note / n_sent if n_sent else 0
        if self.minimum and n_sent and density < self.minimum:
            add(f"note density {density:.1f} per 100 sentences, below the minimum {self.minimum:.1f} "
                f"(target {self.target}): add notes")

        # --- reader data
        data_f = self.book.data_dir / f"{cid}.js"
        if not data_f.exists():
            add(f"reader/data/{cid}.js missing: run build.py {num}")
        else:
            ch = self.check_data(data_f, add) or {}
            built = {}

            def collect(bs):
                for x in bs:
                    if x.get("type") == "box":
                        collect(x.get("blocks", []))
                    for s in x.get("sentences", []) if isinstance(x.get("sentences"), list) else []:
                        built[s.get("id")] = s
            collect(ch.get("blocks", []) + ch.get("appendix", []))
            stale = 0
            for b in source_paras(blocks):
                p = paras.get(b["id"], ({}, ""))[0]
                sl = p.get("s", []) if isinstance(p, dict) else []
                for i, en in enumerate(b["sentences"]):
                    a = sl[i] if i < len(sl) else {}
                    a = {"t": a} if isinstance(a, str) else a if isinstance(a, dict) else {}
                    d = built.get(en["id"])
                    if (not d or d.get("en") != en["html"] or d.get("zh") != a.get("t", "")
                            or d.get("n", "") != a.get("n", "") or d.get("l", "") != a.get("l", "")):
                        stale += 1
            if stale:
                add(f"reader/data/{cid}.js differs from the source or annotations in {stale} sentences: run build.py {num}")
        stats = {"sentences": n_sent, "notes": n_note, "lang": n_lang, "density": density}
        if self.book.cfg["book"].get("kind") == "filing":
            stats["unmatched"] = self.unmatched_numbers(src, files)
        return problems, stats

    @staticmethod
    def unmatched_numbers(src, files):
        """Numbers in the notes of a filing that appear nowhere in the chapter's text and tables:
        either worked out in the note (a difference, a ratio) or a mistake. Listed, not failed."""
        def numbers(text):
            out = set()
            for m in re.finditer(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.,])\d+\.\d+|(?<![\w.,])\d{3,}", text):
                v = m.group(0).replace(",", "")
                if not re.fullmatch(r"(19|20)\d\d", v):  # years
                    out.add(v.rstrip("0").rstrip(".") if "." in v else v)
            return out
        source = json.dumps(src, ensure_ascii=False)
        known = numbers(source)
        notes = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            for path, text in strings(data):
                if path.endswith(".n") or path == "n" or ".n." in path or path.endswith("guide") or "guide[" in path:
                    notes.append((f.name + " " + path, text))
        out = []
        for where, text in notes:
            for v in sorted(numbers(text) - known):
                out.append(f"{where}: {v}")
        return out

    def check_data(self, data_f, add):
        text = data_f.read_text(encoding="utf-8")
        m = re.search(r"\] = (\{.*\});\s*$", text, re.S)
        if not m:
            add(f"{data_f.name}: not in the form build.py writes")
            return
        try:
            ch = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            add(f"{data_f.name}: does not parse ({e})")
            return
        if "undefined" in m.group(1):
            add(f"{data_f.name}: contains 'undefined'")
        if not isinstance(ch.get("guide"), list):
            add(f"{data_f.name}: guide is not a list (the reader would crash)")
        if not ch.get("title_zh"):
            add(f"{data_f.name}: no Chinese chapter title")
        reader = self.book.data_dir.parent

        def walk(blocks):
            for b in blocks:
                t = b.get("type")
                if t not in BLOCK_TYPES:
                    add(f"{data_f.name}: unknown block type {t!r}")
                    continue
                if t in ("para", "quote", "bullets"):
                    for s in b.get("sentences", []):
                        if not s.get("en") or not s.get("zh"):
                            add(f"{data_f.name}: sentence {s.get('id')} lacks text or translation")
                    for ph in b.get("photos", []):
                        if not (reader / ph["src"]).exists():
                            add(f"{data_f.name}: image {ph['src']} missing")
                elif t in ("figure", "table"):
                    if b.get("src") and not (reader / b["src"]).exists():
                        add(f"{data_f.name}: image {b['src']} missing")
                    for row in (b.get("grid") or {}).get("rows", []):
                        if not all(isinstance(c, dict) and "en" in c for c in row):
                            add(f"{data_f.name}: {b.get('id')} grid row not in the built form")
                            break
                    if not b.get("title_zh"):
                        add(f"{data_f.name}: {b.get('id')} has no Chinese title")
                elif t == "box":
                    walk(b.get("blocks", []))
        walk(ch.get("blocks", []))
        walk(ch.get("appendix", []))
        index = self.book.data_dir / "book.js"
        if not index.exists() or f'"{ch.get("id")}"' not in index.read_text(encoding="utf-8"):
            add("reader/data/book.js does not list the chapter: run build.py")
        return ch


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    book, args = open_book(sys.argv[1:])
    if not args:
        sys.exit(__doc__)
    if args == ["all"]:
        nums = sorted(int(re.sub(r"\D", "", p.name)) for p in book.ann_dir.glob("ch*") if p.is_dir())
    else:
        nums = [int(a) for a in args]
    checker = Checker(book)
    bad = 0
    for num in nums:
        problems, st = checker.check(num)
        for p in problems[:40]:
            print(f"  ! {p}")
        if len(problems) > 40:
            print(f"  ! … and {len(problems) - 40} more")
        dens = f", {st['sentences']} sentences, {st['notes']} notes ({st['density']:.1f}/100), {st['lang']} lang" if st else ""
        print(f"ch{num:02d}: {'ok' if not problems else f'{len(problems)} problems'}{dens}")
        if st and st.get("unmatched"):
            print(f"  numbers in notes not found in the chapter ({len(st['unmatched'])}; check each is worked out correctly):")
            for u in st["unmatched"]:
                print(f"    {u}")
        bad += bool(problems)
    if checker.target:
        print(f"target {checker.target} notes per 100 sentences, minimum {checker.minimum:.1f}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
