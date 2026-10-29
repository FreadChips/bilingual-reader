"""PDF pages as regions: the layout model's boxes with their text lines, roles and reading order.

First half of the automatic PDF engine (extract_auto.py assembles the chapter). For every page:

1. The PyMuPDF layout model (pymupdf.layout, a local ONNX model) proposes regions: text,
   section-header, list-item, caption, picture, table, formula, footnote, page-header, page-footer.
   The result is cached in build/layout.json, so region ids never shift: p37.r5 is region 5 of PDF
   page 37 (1-based, as a PDF viewer numbers pages). Delete a page's entry to re-run the model there.
2. Every text line goes to the region that holds most of it; lines no region holds become extra
   regions p37.x1, p37.x2 ...
3. Generic rules give each region a role: skip (running heads, folios, repeated furniture, footnotes),
   heading (level from the PDF outline, else from type size), body, list, quote, margin (outside the
   main text column: glossary notes and side notes), caption, picture, formula, dropcap.
   Tinted panels holding a "Figure N" / "Table N" label become figures; other panels become boxes.
4. book.yaml `auto.rules` (same conditions as layout.roles, plus cls / in_margin / in_panel / lines_max)
   and the chapter's hand labels (labels/chNN.yaml, see references/auto-layout.md) override the roles.
5. Reading order: an XY-cut over the main-column units (regions, figures, boxes): split at a column
   gutter first, then at a horizontal gap, recursively. Margin regions are kept aside.
"""
import json
import re
import unicodedata
from collections import Counter

import pymupdf

from bookkit import as_list, rule_matches

FIGURE_LABEL = r"^(Figure|Fig\.|Table|Exhibit|Chart|Map|Graph)\s*([A-Z]?[-.]?\d+(?:[.\-–]\d+)*[a-z]?)(?=$|[\s.:|—–-])"
HEADING_NUMBER = r"^((?:\d+[.\-])+\d*[a-z]?|\d+)\s+(.*)"
BULLETS = "•●▪■◦‣"
LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})
AUTO_DEFAULTS = {
    "rules": [],
    "figure_label": FIGURE_LABEL,
    "heading_number": HEADING_NUMBER,
    "footnotes": "skip",          # skip | body
    "skip_sections": ["References", "Bibliography", "Works Cited", "Notes", "Index"],  # dropped with their contents
    "box_headings": [],           # regexes: a heading matching one opens a box (LINK IT UP, Case Study ...)
                                  #   that runs to the next horizontal rule or outline heading
    "uncaptioned_pictures": "skip",  # skip | keep (as photos without a caption)
    "table_grid": False,          # tables as cells (tablekit.py): numbers kept, labels translated
    "caption_position": "nearest",  # nearest | below | above: where a figure label sits relative to its
                                  #   picture; papers set captions below figures and tables
    "furniture_share": 0.3,       # text repeated near the top/bottom of this share of pages is dropped
    "margin_overlap": 0.3,        # a region overlapping the main column by less than this is a margin note
    "box_label_max": 40,          # a short first line set apart in a panel is the box label (FYI, Case Study)
}


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def same_title(a, b):
    """Outline entry and heading text (both normalised) name the same heading: equal, or one a prefix
    of the other (a heading broken over a line, an outline entry cut short) of similar length."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    # "FYI Stocks and Flows" in the outline, the label printed apart (a short label, not a sentence)
    if len(short) >= 10 and long_.endswith(short) and len(long_) - len(short) <= 12:
        return True
    return len(short) >= 8 and long_.startswith(short) and len(short) >= 0.7 * len(long_)


def auto_config(cfg):
    out = dict(AUTO_DEFAULTS)
    out.update(cfg.get("auto") or {})
    lay = cfg.get("layout") or {}
    out["bold_markers"] = lay.get("bold_markers") or ["Bold", "Bd", "Black", "Semibold", "Heavy"]
    out["italic_markers"] = lay.get("italic_markers") or ["Italic", "Obl", "It"]
    out["symbol_fonts"] = tuple(as_list(lay.get("symbol_fonts") or []))
    out["symbol_map"] = str.maketrans(lay.get("symbol_map") or {})
    # math fonts that encode Greek letters and operators as control characters, each in its own way:
    # {exact font name: {code: character}}
    out["font_maps"] = {f: str.maketrans(m) for f, m in (lay.get("font_maps") or {}).items()}
    # the same, by glyph name ({"H9266": "π"}): stable where a font's codes change from page to page
    out["glyph_maps"] = dict(lay.get("glyph_maps") or {})
    out["page_offset"] = lay.get("page_offset")
    out["_label"] = re.compile(out["figure_label"], re.I)
    out["_hnum"] = re.compile(out["heading_number"])
    return out


# ---------------------------------------------------------------- model cache


class LayoutCache:
    """build/layout.json: {"37": [[x0, y0, x1, y1, class], ...]} in (top, left) order."""

    def __init__(self, book, doc):
        self.path = book.root / "build" / "layout.json"
        self.doc = doc
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.dirty = False
        self.model = False

    def boxes(self, pno):
        key = str(pno + 1)
        if key not in self.data:
            if not self.model:
                import pymupdf.layout  # noqa: F401  (loads the ONNX model and registers page.get_layout)
                self.model = True
            page = self.doc[pno]
            page.get_layout()
            found = []
            for b in page.layout_information or []:
                if b[2] - b[0] > 2 and b[3] - b[1] > 2:
                    found.append([round(v, 1) for v in b[:4]] + [b[4]])
            found.sort(key=lambda b: (round(b[1]), b[0]))
            self.data[key] = found
            self.dirty = True
        return self.data[key]

    def save(self):
        if self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines = [f'"{k}": {json.dumps(v)}' for k, v in sorted(self.data.items(), key=lambda kv: int(kv[0]))]
            self.path.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")
            self.dirty = False


# ---------------------------------------------------------------- text lines


def span_markup(span, acfg):
    text, font = span["text"], span["font"]
    if not text.strip():
        return text
    if any(m in font for m in acfg["bold_markers"]) or span["flags"] & 16:
        return f"<b>{text}</b>"
    if any(m in font for m in acfg["italic_markers"]) or span["flags"] & 2:
        return f"<i>{text}</i>"
    return text


def span_text(chars):
    """A span's text from its characters, without the phantom space some PDFs put after a ligature
    ("ﬁ rst", "infl ation"): a space after a ligature glyph with no gap before the next letter."""
    out = []
    for k, c in enumerate(chars):
        if c["c"] == " " and 0 < k < len(chars) - 1 and chars[k - 1]["c"] in "ﬀﬁﬂﬃﬄ" \
                and chars[k + 1]["bbox"][0] - chars[k - 1]["bbox"][2] < 0.06 * (c["bbox"][3] - c["bbox"][1]):
            continue
        out.append(c["c"])
    return "".join(out)


def unspace(spans):
    """Letter-spaced type ("K E Y  C O N C E P T S", one span per letter or not): one span with the
    letters closed up, keeping only the wider gaps as word breaks. None when the line is ordinary."""
    text = "".join(s["text"] for s in spans)
    if len(text.strip()) < 5 or not re.fullmatch(r"\s*(\S {1,3})+\S\s*", text):
        return None
    letters = [c for s in spans for c in s["_chars"] if c["c"].strip()]
    gaps = [b["bbox"][0] - a["bbox"][2] for a, b in zip(letters, letters[1:])]
    med = sorted(gaps)[len(gaps) // 2]
    joined = letters[0]["c"] + "".join((" " if g > 1.6 * med + 0.5 else "") + b["c"]
                                       for g, b in zip(gaps, letters[1:]))
    return [dict(spans[0], text=joined.translate(LIGATURES))]


def make_line(spans, bbox, block, acfg):
    text = "".join(s["text"] for s in spans)
    fonts = Counter()
    for s in spans:
        fonts[(s["font"], round(s["size"], 1))] += len(s["text"].strip())
    (font, size), _ = fonts.most_common(1)[0]
    marks = [span_markup(s, acfg) for s in spans]
    bold = all(m.startswith("<b>") for m, s in zip(marks, spans) if s["text"].strip())
    # overbars: a minus or macron raised over the glyph before it ("Ḡ", "Ī") becomes a combining macron
    bars = set()
    for k in range(1, len(spans)):
        s, p = spans[k], spans[k - 1]
        if (s["text"].strip() in ("−", "–", "-", "¯", "‾") and p["text"].strip()
                and p["bbox"][0] - 1 <= s["bbox"][0] < p["bbox"][2] - 0.5
                and s["bbox"][1] < p["bbox"][1] - 0.4 * size):
            marks[k - 1] = unicodedata.normalize(
                "NFC", re.sub(r"([^\s<>])((?:\s|</?\w+>)*)$", "\\1\u0304\\2", marks[k - 1]))
            marks[k] = ""
            bars.add(k)
    if bars:
        text = unicodedata.normalize("NFC", "".join(
            (re.sub(r"(\S)(\s*)$", "\\1\u0304\\2", s["text"]) if k + 1 in bars else s["text"])
            for k, s in enumerate(spans) if k not in bars))
    # subscripts and superscripts: smaller type set below or above the line's baseline ("m_t", "x²")
    base = [s["origin"][1] for s in spans if (s["font"], round(s["size"], 1)) == (font, size)]
    if base and len(spans) > 1:
        for k, s in enumerate(spans):
            dy = s["origin"][1] - base[0]
            if k not in bars and s["text"].strip() and s["size"] <= 0.85 * size:
                if dy >= 0.1 * size:
                    marks[k] = f"<sub>{marks[k]}</sub>"
                elif dy <= -0.15 * size:
                    marks[k] = f"<sup>{marks[k]}</sup>"
    markup = re.sub(r"</(sub|sup)>(\s*)<\1>", r"\2", "".join(marks))
    return {"text": text, "markup": markup, "font": font, "size": size,
            "bbox": tuple(bbox), "block": block, "spans": spans, "bold": bold}


_GLYPHS = {}


def font_glyphs(doc, xref):
    """{code: (glyph name, glyph id)} from a simple font's /Differences encoding."""
    key = (id(doc), xref)
    if key not in _GLYPHS:
        table = {}
        kind, val = doc.xref_get_key(xref, "Encoding")
        if kind == "xref":
            kind, val = doc.xref_get_key(int(val.split()[0]), "Differences")
        elif kind == "dict":
            m = re.search(r"/Differences\s*(\[[^]]*\])", val)
            val = m.group(1) if m else ""
        else:
            val = ""
        code, names = 0, {}
        for tok in re.findall(r"\d+|/[^\s/\[\]]+", val or ""):
            if tok[0] == "/":
                names[code] = tok[1:]
                code += 1
            else:
                code = int(tok)
        if names:
            try:
                font = pymupdf.Font(fontbuffer=doc.extract_font(xref)[3])
                gid = lambda n: pymupdf.mupdf.fz_encode_character_by_glyph_name(font.this, n)
            except Exception:
                gid = lambda n: None
            table = {c: (n, gid(n)) for c, n in names.items()}
        _GLYPHS[key] = table
    return _GLYPHS[key]


def decode_glyphs(page, blocks, gmap):
    """Rewrite characters of fonts whose glyph names are in gmap. Fonts on one page can share a name
    while encoding the same code differently; the glyph id from the text trace tells them apart."""
    cands = {}
    for xref, _, _, base, *_ in page.get_fonts():
        table = font_glyphs(page.parent, xref)
        if any(n in gmap for n, _ in table.values()):
            cands.setdefault(base.split("+")[-1], []).append(table)
    if not cands:
        return
    gids = {}
    for s in page.get_texttrace():
        for ch in s["chars"]:
            gids[(round(ch[2][0], 1), round(ch[2][1], 1))] = ch[1]
    for b in blocks:
        for l in b.get("lines", []):
            for s in l["spans"]:
                name = s["font"].split("+")[-1]
                tables = cands.get(name) or [t for n, ts in cands.items() if len(name) >= 24 and n.startswith(name)
                                             for t in ts]
                if not tables:
                    continue
                for c in s["chars"]:
                    code = ord(c["c"]) if c["c"] != "�" else None
                    gid = gids.get((round(c["origin"][0], 1), round(c["origin"][1], 1)))
                    hits = {t[code][0] for t in tables if code in t and (gid is None or t[code][1] == gid)}
                    if code is None or not hits:  # no usable code: find the glyph by its id alone
                        hits = {n for t in tables for n, g in t.values() if gid is not None and g == gid}
                    if len(hits) == 1 and (h := hits.pop()) in gmap:
                        c["c"] = gmap[h]


def vector_bars(page, blocks):
    """Overbars drawn as a short stroke just above one letter ("Ȳ" set as Y plus a line) become a
    combining macron on that letter. A stroke with a glyph right above it is a fraction bar."""
    dark = lambda c: c is not None and sum(c[:3]) < 1.5
    bars = [d["rect"] for d in page.get_drawings() if d["rect"].height <= 1.0 and 0.8 <= d["rect"].width <= 14
            and (dark(d.get("color")) or dark(d.get("fill")))]
    if not bars:
        return
    chars = [(c, s["size"]) for b in blocks for l in b.get("lines", []) for s in l["spans"] for c in s["chars"]
             if c["c"].strip()]
    for c, size in chars:
        if not c["c"].isalnum():
            continue
        x0, y0, x1, _ = c["bbox"]
        for r in bars:
            if (x0 <= (r.x0 + r.x1) / 2 <= x1 and 0.3 * (x1 - x0) <= r.width <= 1.6 * (x1 - x0) + 1
                    and y0 - 0.25 * size <= r.y1 <= y0 + 0.15 * size  # a radical's top sits higher
                    and not any(o["bbox"][0] < r.x1 and o["bbox"][2] > r.x0
                                and r.y0 - 0.6 * size <= o["bbox"][3] <= r.y0 + 0.5 for o, _ in chars)):
                c["c"] = unicodedata.normalize("NFC", c["c"] + "̄")
                break


def page_lines(page, acfg):
    lines, images = [], []
    blocks = page.get_text("rawdict")["blocks"]
    if acfg["glyph_maps"]:
        decode_glyphs(page, blocks, acfg["glyph_maps"])
    vector_bars(page, blocks)
    for bi, b in enumerate(blocks):
        if b["type"] != 0:
            images.append(pymupdf.Rect(b["bbox"]))
            continue
        for l in b["lines"]:
            if abs(l["dir"][1]) > 0.1:
                continue  # rotated text: photo credits, axis titles
            for s in l["spans"]:
                s["_chars"] = s.pop("chars")
                s["text"] = span_text(s["_chars"]).translate(LIGATURES)
                # a typesetting code left in the text layer ("<h1 The Accumulation of Capital"); a browser
                # would read it as a tag and swallow the heading
                s["text"] = re.sub(r"(^|\s)<h[1-6]\s+", r"\1", s["text"])
                fmap = acfg["font_maps"].get(s["font"].split("+")[-1])
                if fmap:
                    s["text"] = s["text"].translate(fmap)
            spans = [s for s in l["spans"] if s["text"]]
            # a footnote mark: superscript digits right after punctuation ("8 percent.²"); not an exponent,
            # which follows a digit, a letter or a bracket ("(zL)¹⁻ᵅ")
            spans = [s for k, s in enumerate(spans)
                     if not (s["flags"] & 1 and re.fullmatch(r"\d{1,3}", s["text"].strip()) and k
                             and spans[k - 1]["text"].rstrip()[-1:] in ".,;:”’\"")]
            spans = unspace(spans) or spans
            # a star or prime drawn back over the space before it ("r ∗": the star set across the italic
            # r's trailing space) marks the letter, not a new word
            for s, n in zip(spans, spans[1:]):
                sc, nc = s.get("_chars") or [], n.get("_chars") or []
                if s["text"].endswith(" ") and sc and sc[-1]["c"] == " " and nc and nc[0]["c"] in "∗*′":
                    w = sc[-1]["bbox"][2] - sc[-1]["bbox"][0]
                    if w > 0 and nc[0]["bbox"][0] < sc[-1]["bbox"][2] - 0.4 * w:
                        s["text"] = s["text"][:-1]
            if acfg["symbol_fonts"]:
                for s in spans:
                    if s["font"].startswith(acfg["symbol_fonts"]):
                        s["text"] = s["text"].translate(acfg["symbol_map"])
            if not "".join(s["text"] for s in spans).strip():
                continue
            lines.append(make_line(spans, l["bbox"], bi, acfg))
    return join_split_labels(lines, acfg), images


def page_words(page, acfg, clip):
    """Words inside clip as (x0, y0, x1, y1, text, bold), decoded like the running text (glyph_maps,
    font_maps, symbol fonts, ligatures), so a table keeps its "=" and "+" signs. Words are split by
    the gaps between letters, not by space characters: a phantom space after a ligature ("Outfl ow")
    has no width."""
    blocks = page.get_text("rawdict", clip=clip)["blocks"]
    if acfg["glyph_maps"]:
        decode_glyphs(page, blocks, acfg["glyph_maps"])
    words = []
    for b in blocks:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            if abs(l["dir"][1]) > 0.1:
                continue
            cur = None
            for s in l["spans"]:
                fmap = acfg["font_maps"].get(s["font"].split("+")[-1]) or {}
                sym = acfg["symbol_fonts"] and s["font"].startswith(acfg["symbol_fonts"])
                bold = bool(any(m in s["font"] for m in acfg["bold_markers"]) or s["flags"] & 16)
                for ch in s["chars"]:
                    c = ch["c"].translate(fmap)
                    if sym:
                        c = c.translate(acfg["symbol_map"])
                    c = c.translate(LIGATURES)
                    x0, y0, x1, y1 = ch["bbox"]
                    if not c.strip():
                        continue
                    if cur and x0 - cur[2] < 0.15 * s["size"]:
                        cur[2], cur[1], cur[3] = max(cur[2], x1), min(cur[1], y0), max(cur[3], y1)
                        cur[4] += c
                        cur[5] = cur[5] and bold
                    else:
                        cur = [x0, y0, x1, y1, c, bold]
                        words.append(cur)
    return [tuple(w) for w in words if pymupdf.Rect(w[:4]).intersects(clip)]


def join_split_labels(lines, acfg):
    """A label set as two text lines ("FIGURE" | "1-2" beside or under it) becomes one line."""
    merged, used = {}, set()
    for l in lines:
        word = l["text"].strip()
        if not re.search(r"\d", word) and acfg["_label"].match(word + " 1"):
            x1, y0, y1 = l["bbox"][2], l["bbox"][1], l["bbox"][3]
            nums = [o for o in lines if o is not l and id(o) not in used
                    and re.match(r"[A-Z]?[-.]?\d", o["text"].strip())
                    and ((abs(o["bbox"][1] - y0) < 6 and 0 <= o["bbox"][0] - x1 < 30)            # beside it
                         or (0 <= o["bbox"][1] - y1 < 4 and abs(o["bbox"][0] - l["bbox"][0]) < 6))]  # under it
            if nums:
                o = min(nums, key=lambda o: abs(o["bbox"][1] - y0) + abs(o["bbox"][0] - x1))
                used.add(id(o))
                bbox = pymupdf.Rect(l["bbox"]) | pymupdf.Rect(o["bbox"])
                merged[id(l)] = dict(l, text=word + " " + o["text"].strip(), spans=l["spans"] + o["spans"],
                                     markup=l["markup"].rstrip() + " " + o["markup"].strip(), bbox=tuple(bbox))
    return [merged.get(id(l), l) for l in lines if id(l) not in used]


def body_style(doc, pages):
    """The commonest (font, size) by characters, and the main column's x-span on odd and even pages."""
    chars, xs = Counter(), {0: [], 1: []}
    for pno in pages:
        for b in doc[pno].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    if s["text"].strip():
                        chars[(s["font"], round(s["size"], 1))] += len(s["text"])
    font, size = chars.most_common(1)[0][0] if chars else ("", 10.0)
    for pno in pages:
        for b in doc[pno].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                if any(s["font"] == font and round(s["size"], 1) == size for s in l["spans"]):
                    xs[pno % 2].append((l["bbox"][0], l["bbox"][2]))
    spans = {}
    for parity, v in xs.items():
        if len(v) < 20:
            continue
        x0 = sorted(a for a, _ in v)[len(v) // 20]
        x1 = sorted(b for _, b in v)[len(v) * 19 // 20]
        spans[parity] = (x0, x1)
    if len(spans) == 1:
        spans[1 - next(iter(spans))] = next(iter(spans.values()))
    return {"font": font, "size": size, "spans": spans}


# ---------------------------------------------------------------- regions


def rect_of(lines):
    r = pymupdf.Rect(lines[0]["bbox"])
    for l in lines[1:]:
        r |= pymupdf.Rect(l["bbox"])
    return r


def first_line(region):
    return region["lines"][0] if region["lines"] else {"text": "", "font": "", "size": 0, "bbox": tuple(region["bbox"])}


def region_text(region):
    return " ".join(l["text"].strip() for l in region["lines"])


def make_regions(pno, boxes, lines, images, page_rect):
    regions = [{"id": f"p{pno + 1}.r{k + 1}", "pno": pno, "bbox": pymupdf.Rect(b[:4]), "cls": b[4], "lines": []}
               for k, b in enumerate(boxes)]
    # pictures the model missed: image blocks no picture region covers (not page backgrounds)
    pics = [r["bbox"] for r in regions if r["cls"] in ("picture", "table")]
    extra, k = [], 0
    for im in images:
        if im.width < 40 or im.height < 30 or im.get_area() > 0.6 * page_rect.get_area():
            continue
        if any((p & im).get_area() > 0.5 * im.get_area() for p in pics if p.intersects(im)):
            continue
        if sum(1 for l in lines if im.contains(pymupdf.Rect(l["bbox"]))) >= 3:
            continue  # text on a tinted image: a panel, not a picture
        k += 1
        pics.append(im)
        extra.append({"id": f"p{pno + 1}.i{k}", "pno": pno, "bbox": im & page_rect, "cls": "picture", "lines": []})
    orphans = {}
    for l in lines:  # text over a picture the model missed is not part of it (a caption band, a footer)
        lr = pymupdf.Rect(l["bbox"])
        area = max(lr.get_area(), 0.01)
        best, best_a = None, 0.0
        for r in regions:
            a = (r["bbox"] & lr).get_area() if r["bbox"].intersects(lr) else 0.0
            if a > best_a:
                best, best_a = r, a
        if best is not None and best_a >= 0.4 * area:
            best["lines"].append(l)
        else:
            orphans.setdefault(l["block"], []).append(l)
    for k, (_, ls) in enumerate(sorted(orphans.items())):
        regions.append({"id": f"p{pno + 1}.x{k + 1}", "pno": pno, "bbox": rect_of(ls), "cls": "text",
                        "lines": ls, "orphan": True})
    regions.extend(extra)
    for r in regions:
        r["lines"].sort(key=lambda l: (round(l["bbox"][3] / 2), l["bbox"][0]))
    return regions


def grow_pictures(page, regions):
    """The model may box only the labels of a vector drawing ("Flow", "Stock" over a bathtub): grow each
    picture region over the drawing paths that touch it, leaving out frames and tints around it."""
    pics = [r for r in regions if r["cls"] in ("picture", "table")]
    if not pics:
        return
    W, H = page.rect.width, page.rect.height
    paths = [pymupdf.Rect(d["rect"]) for d in page.get_drawings()
             if d["rect"].get_area() < 0.3 * W * H and not (d["rect"].height <= 2 and d["rect"].width >= 0.6 * W)]
    for r in pics:
        rect = pymupdf.Rect(r["bbox"])
        changed = True
        while changed:
            changed = False
            for p in paths:
                if p.contains(rect) or (rect.contains(p)):
                    continue  # a frame or tint around the picture, or a path already inside it
                if p.intersects(rect + (-3, -3, 3, 3)):
                    rect |= p
                    changed = True
        r["bbox"] = rect


def find_rules(page):
    """Horizontal rules (thin lines across much of the page): where a box opened by a heading ends."""
    W = page.rect.width
    return sorted({round(d["rect"].y0, 1) for d in page.get_drawings()
                   if d["rect"].height <= 2 and d["rect"].width >= 0.6 * W})


def find_panels(page, images, regions):
    """Tinted, framed or image-backed rectangles that hold text: figure panels and side boxes."""
    W, H = page.rect.width, page.rect.height
    cands = []
    for d in page.get_drawings():
        r, fill = d["rect"], d.get("fill")
        tinted = fill and not all(c > 0.97 for c in fill)
        framed = d.get("color") is not None and d["type"] in ("s", "fs") and not tinted  # a frame around a box
        if (tinted or framed) and r.width > 0.2 * W and r.height > 40 and r.get_area() < 0.8 * W * H:
            cands.append(pymupdf.Rect(r))
    for r in images:
        if r.width > 0.2 * W and r.height > 40 and r.get_area() < 0.8 * W * H:
            inside = [x for x in regions if x["lines"] and r.contains(x["bbox"]) and x["cls"] != "picture"]
            if sum(len(x["lines"]) for x in inside) >= 3:
                cands.append(pymupdf.Rect(r))
    cands.sort(key=lambda r: -r.get_area())
    panels = []
    for r in cands:  # keep the outermost of nested rectangles (a border around a tint, a header band)
        if not any(p.contains(r + (-2, -2, 2, 2)) or p.contains(r) for p in panels):
            panels.append(r)
    out = []
    for p in panels:
        members = [x for x in regions if p.contains(pymupdf.Point((x["bbox"].x0 + x["bbox"].x1) / 2,
                                                                   (x["bbox"].y0 + x["bbox"].y1) / 2))]
        if sum(len(x["lines"]) for x in members) >= 3 or any(x["cls"] in ("picture", "table") for x in members):
            out.append({"rect": p, "members": members})
    return out


# ---------------------------------------------------------------- the chapter's pages


class PageSet:
    """All pages of one chapter as regions with roles; see the module docstring."""

    def __init__(self, book, doc, cache, first, last, chapter_title, toc, acfg, labels, style):
        self.doc, self.first, self.last = doc, first, last
        self.acfg, self.labels, self.style = acfg, labels, style
        self.toc = toc
        self.chapter_title = norm(chapter_title)
        self.pages = {}
        self.notes = []  # things worth a look in the overlay: (pno, region id, message)
        for pno in range(first, last + 1):
            page = doc[pno]
            lines, images = page_lines(page, acfg)
            regions = make_regions(pno, cache.boxes(pno), lines, images, page.rect)
            self.split_mixed(pno, regions)
            self.split_headings(pno, regions)
            grow_pictures(page, regions)
            self.pages[pno] = {"regions": regions, "images": images, "page": page,
                               "panels": find_panels(page, images, regions), "rules": find_rules(page)}
        self.furniture = self.find_furniture()
        for pno, pg in self.pages.items():
            self.assign_roles(pno, pg)
        self.apply_rules_and_labels()
        self.heading_levels()
        for pno, pg in self.pages.items():
            self.group(pno, pg)
            self.order(pno, pg)

    def split_mixed(self, pno, regions):
        """The model sometimes draws one region over a margin note and the paragraph beside it:
        lines outside the main column go to a region of their own (p39.r8m)."""
        sp = self.style["spans"].get(pno % 2)
        if not sp:
            return
        for r in list(regions):
            if r["cls"] in ("page-header", "page-footer", "picture", "table") or len(r["lines"]) < 2:
                continue
            normal = [l for l in r["lines"] if l["size"] < 1.8 * self.style["size"]]  # drop caps stay
            out = [l for l in normal
                   if max(0.0, min(l["bbox"][2], sp[1]) - max(l["bbox"][0], sp[0]))
                   < self.acfg["margin_overlap"] * max(l["bbox"][2] - l["bbox"][0], 1)]
            inside = [l for l in normal if l not in out]
            if out and inside:
                # ... and only lines beside the others, not a short last line of the same column
                x0, x1 = min(l["bbox"][0] for l in inside), max(l["bbox"][2] for l in inside)
                out = [l for l in out if max(0.0, min(l["bbox"][2], x1) - max(l["bbox"][0], x0))
                       < 0.3 * max(l["bbox"][2] - l["bbox"][0], 1)]
            if out and inside:
                r["lines"] = [l for l in r["lines"] if l not in out]
                r["bbox"] = rect_of(r["lines"])
                regions.append({"id": r["id"] + "m", "pno": pno, "bbox": rect_of(out), "cls": "text", "lines": out})

    def split_headings(self, pno, regions):
        """The model sometimes draws one region over a heading line and the paragraph under it
        ("7-2b Using the Supply Curve …" | "Just as consumer surplus …"): when the first lines are an
        outline entry word for word, all in one font, and the next line opens in another font, they
        become a region of their own and the rest goes to p173.r5b. (A figure title run on into its
        caption in the same type is left alone.)"""
        for r in list(regions):
            if r["cls"] not in ("text", "section-header", "list-item") or len(r["lines"]) < 2:
                continue
            for k in range(1, min(3, len(r["lines"]) - 1) + 1):
                head, nxt = r["lines"][:k], r["lines"][k]
                fonts = {s["font"] for l in head for s in l["spans"] if s["text"].strip()}
                first = next((s["font"] for s in nxt["spans"] if s["text"].strip()), None)
                text = norm(" ".join(l["text"] for l in head))
                if len(fonts) == 1 and first not in fonts and len(text) >= 3 \
                        and any(norm(title) == text for _, title in self.toc):
                    rest = r["lines"][k:]
                    r["lines"], r["bbox"] = head, rect_of(head)
                    regions.append({"id": r["id"] + "b", "pno": pno, "bbox": rect_of(rest), "cls": "text", "lines": rest})
                    break

    def note(self, region, msg):
        self.notes.append((region["pno"], region["id"], msg))

    # ------------------------------------------------------------ roles
    def find_furniture(self):
        seen = Counter()
        for pno, pg in self.pages.items():
            H = pg["page"].rect.height
            keys = set()
            for r in pg["regions"]:
                if r["lines"] and (r["bbox"].y1 < 0.1 * H or r["bbox"].y0 > 0.9 * H):
                    keys.add(re.sub(r"\d+", "#", norm(region_text(r)) or "-"))
            seen.update(keys)
        need = max(3, self.acfg["furniture_share"] * len(self.pages))
        return {k for k, c in seen.items() if c >= need}

    def main_span(self, pno):
        sp = self.style["spans"].get(pno % 2)
        return sp

    def in_margin(self, r):
        sp = self.main_span(r["pno"])
        if not sp or r["cls"] in ("page-header", "page-footer"):
            return False
        x0, x1 = r["bbox"].x0, r["bbox"].x1
        overlap = max(0.0, min(x1, sp[1]) - max(x0, sp[0]))
        return overlap < self.acfg["margin_overlap"] * max(x1 - x0, 1)

    def toc_level(self, text):
        t = norm(text)
        if len(t) < 3:
            return None
        for lvl, title in self.toc:
            if same_title(norm(title), t):
                return lvl
        return None

    def assign_roles(self, pno, pg):
        body = self.style["size"]
        H = pg["page"].rect.height
        for r in pg["regions"]:
            r["role"], r["level"] = "body", None
            cls, text = r["cls"], region_text(r)
            r["margin"] = self.in_margin(r)
            if cls in ("page-header", "page-footer"):
                r["role"] = "skip"
            elif not r["lines"]:
                r["role"] = "picture" if cls in ("picture", "table") else "skip"
            elif cls in ("picture", "table"):
                r["role"] = "picture"
            elif (r["bbox"].y1 < 0.1 * H or r["bbox"].y0 > 0.9 * H) and \
                    re.sub(r"\d+", "#", norm(text) or "-") in self.furniture:
                r["role"] = "skip"
            elif pno - self.first < 2 and (norm(text) == self.chapter_title or
                                           (len(norm(text)) > 6 and norm(text) in self.chapter_title
                                            and first_line(r)["size"] >= 1.3 * body)
                                           or re.fullmatch(r"(chapter|part|unit|lecture|lesson)\d+", norm(text))):
                r["role"] = "skip"  # the chapter title and number on the opening pages
            elif r["margin"]:
                r["role"] = "margin"
            elif self.acfg["footnotes"] == "skip" and (cls == "footnote" or (
                    r["bbox"].y0 > 0.7 * H and first_line(r)["size"] < 0.9 * body
                    and re.match(r"\s*(\d{1,2}|[*†‡§])\s*[A-Za-z(“\"]", text))):
                r["role"] = "skip"  # a footnote: small type at the foot of the page, opening with its mark
            elif cls == "formula":
                r["role"] = "formula"
            elif cls in ("section-header", "title"):
                r["role"] = "heading"
            elif cls == "caption":
                r["role"] = "caption"
            elif cls == "list-item":
                r["role"] = "list"
            sp = self.main_span(pno)
            if r["role"] == "body" and sp and len(r["lines"]) >= 2 and r["bbox"].width > 0.5 * (sp[1] - sp[0]) \
                    and r["bbox"].x0 - sp[0] >= 15 and sp[1] - r["bbox"].x1 >= 15:
                r["role"] = "quote"  # indented on both sides: a displayed quotation
            # a TOC entry set as a text block is still a heading
            if r["role"] in ("body", "caption") and len(r["lines"]) <= 3 and self.toc_level(text):
                r["role"] = "heading"
            if r["role"] == "heading":
                fl = first_line(r)
                long_prose = len(text) > 150 or (text.rstrip().endswith(".") and len(text) > 80 and not fl["bold"])
                # a heading never starts in lower case: that is a sentence running on from the last column
                # ... nor ends with a full stop: "Chapters 7 and 8.)" is the tail of a sentence, unless it is
                # in title case ("Income Inequality Is Not Rising Globally. It's Falling.", a news headline)
                long_words = re.findall(r"[A-Za-z][\w’']{3,}", text)
                title_case = len(long_words) >= 3 and sum(w[0].isupper() for w in long_words) >= 0.8 * len(long_words)
                if (long_prose or text.lstrip()[:1].islower()
                        or (re.search(r"\w\.\)?$", text.strip()) and not title_case)) and not self.toc_level(text):
                    r["role"] = "body"
            # big digits on the opening page (a chapter number) or a lone big letter (a drop cap)
            if r["role"] in ("body", "heading", "skip", "margin") and len(text) <= 3 \
                    and first_line(r)["size"] >= 1.8 * body:
                r["role"] = "dropcap" if re.fullmatch(r"[A-Z]", text.strip()) else "skip"
        self.split_dropcaps(pg)
        self.mark_run_in_heads(pg)
        if pno - self.first < 2:  # a "CHAPTER" / "Chapter 3" label set inside another region or line
            pat = r"(chapter|part|unit|lecture|lesson)\d*"
            for r in pg["regions"]:
                lines = []
                for l in r["lines"]:
                    keep = [s for s in l["spans"] if not re.fullmatch(pat, norm(s["text"]))]
                    for word in ("chapter", "part", "unit", "lecture", "lesson"):  # "C","H","A",... spans
                        acc, k = "", 0
                        while k < len(keep) and len(acc) < len(word) and word.startswith(acc + norm(keep[k]["text"])):
                            acc += norm(keep[k]["text"])
                            k += 1
                        if acc == word:
                            keep = keep[k:]
                    if len(keep) < len(l["spans"]) and "".join(s["text"] for s in keep).strip():
                        l = make_line(keep, l["bbox"], l["block"], self.acfg)
                    if not re.fullmatch(pat, norm(l["text"])):
                        lines.append(l)
                if lines:
                    r["lines"] = lines
                elif r["lines"]:
                    r["role"] = "skip"  # the region was only the label

    def mark_run_in_heads(self, pg):
        """A run-in heading opening a paragraph in another typeface ("Used Goods When the Topps ...")
        is marked bold, so it reads as a heading in the text."""
        family = lambda f: f.split("-")[0].split("+")[-1]
        body = family(self.style["font"])
        for r in pg["regions"]:
            if r["role"] not in ("body", "list") or not r["lines"]:
                continue
            l = r["lines"][0]
            sp = [s for s in l["spans"] if s["text"].strip()]
            if len(sp) >= 2 and family(sp[0]["font"]) != body and family(sp[1]["font"]) == body \
                    and 3 <= len(sp[0]["text"].strip()) <= 60 and not sp[0]["flags"] & 2 \
                    and not sp[0]["text"].strip().endswith((".", ",", ";")):
                spans = [dict(s, flags=s["flags"] | 16) if s is sp[0] else s for s in l["spans"]]
                r["lines"][0] = make_line(spans, l["bbox"], l["block"], self.acfg)

    def split_dropcaps(self, pg):
        """A drop cap is often a line of its own inside the paragraph's region: pull it out."""
        body = self.style["size"]
        for r in list(pg["regions"]):
            if r["role"] == "dropcap":
                cands = [x for x in pg["regions"] if x is not r and x["role"] in ("body", "list")
                         and x["bbox"].x0 >= r["bbox"].x0 - 4 and x["bbox"].y0 <= r["bbox"].y1
                         and x["bbox"].y1 >= r["bbox"].y0]
                if cands:
                    host = min(cands, key=lambda x: x["bbox"].x0 - r["bbox"].x1)
                    host["dropcap"] = region_text(r).strip()
                r["role"] = "skip"
                continue
            if r["role"] not in ("body", "list"):
                continue
            for l in list(r["lines"]):
                if re.fullmatch(r"[A-Z]", l["text"].strip()) and l["size"] >= 1.8 * body and len(r["lines"]) > 1:
                    r["lines"].remove(l)
                    r["dropcap"] = l["text"].strip()

    def extra_conditions(self):
        return {
            "cls": lambda line: line["_region"]["cls"],
            "in_margin": lambda line: line["_region"]["margin"],
            "in_panel": lambda line: line["_region"].get("_in_panel", False),
        }

    def apply_rules_and_labels(self):
        rules = self.acfg["rules"]
        extra = self.extra_conditions()
        for pg in self.pages.values():
            for p in pg["panels"]:
                for m in p["members"]:
                    m["_in_panel"] = True
            for r in pg["regions"]:
                if rules and r["lines"]:
                    line = dict(first_line(r), _region=r)
                    for rule in rules:
                        conds = {k: v for k, v in rule.items() if k not in ("role", "level", "note", "lines_max")}
                        if "lines_max" in rule and len(r["lines"]) > rule["lines_max"]:
                            continue
                        if rule_matches(line, conds, extra):
                            r["role"] = rule["role"]
                            if "level" in rule:
                                r["level"] = rule["level"]
                            break
                lab = self.labels.get(r["id"])
                if lab is None:
                    continue
                lab = {"role": lab} if isinstance(lab, str) else lab
                if "role" in lab:
                    r["role"] = lab["role"]
                if "level" in lab:
                    r["level"] = lab["level"]
                for k in ("order", "after", "join", "box", "label", "keep", "continue", "grid"):
                    if k in lab:
                        r["lab_" + k] = lab[k]
                r["labelled"] = True
        known = {r["id"] for pg in self.pages.values() for r in pg["regions"]}
        for key in self.labels:
            if key not in known:
                raise SystemExit(f"labels: no region {key} in this chapter (see build/layout/<ch>.txt)")

    def heading_levels(self):
        """Outline level when the heading is in the PDF outline, else by type size."""
        heads = [r for pg in self.pages.values() for r in pg["regions"] if r["role"] == "heading"]
        by_toc = {}
        for r in heads:
            lvl = self.toc_level(region_text(r))
            if lvl:
                r["toc"] = True
                by_toc.setdefault(min(lvl, 2), []).append(first_line(r)["size"])
        sizes = sorted({first_line(r)["size"] for r in heads}, reverse=True)
        for r in heads:
            if r.get("level"):
                continue
            lvl = self.toc_level(region_text(r))
            size = first_line(r)["size"]
            if lvl:
                r["level"] = min(lvl, 2)
            elif by_toc.get(1):
                r["level"] = 1 if size >= min(by_toc[1]) - 0.3 else 2
            else:
                r["level"] = 1 if size >= sizes[0] - 0.3 else 2
            if self.toc and not lvl and not r.get("labelled"):
                self.note(r, "heading not in the PDF outline")
        found = {norm(region_text(r)) for r in heads}
        for lvl, title in self.toc:
            t = norm(title)
            if not any(same_title(t, f) for f in found):
                self.notes.append((self.first, "-", f"outline entry not found as a heading: {title}"))

    # ------------------------------------------------------------ figures, panels, boxes
    def label_match(self, r):
        if not r["lines"]:
            return None
        m = self.acfg["_label"].match(first_line(r)["text"].strip())
        if not m:
            return None
        rest = first_line(r)["text"].strip()[m.end():]
        if rest and not re.match(r"\s*([.:|—–-]|[A-Z(\"“‘]|$)", rest):
            return None  # "Figure 3 shows ..." is a sentence, not a label
        if re.match(r"\s*\([^)]{1,30}\)\s+[a-z]", rest):
            return None  # "Fig. 6 (middle) shows ...": a sentence pointing at a panel
        return m

    def inner_label(self, picture):
        """A label printed among the picture's own text ("FIGURE A-2" inside the chart, or a caption
        the model drew into the picture): the label match and a pseudo region of the label line and
        the lines under it, which are the title and caption."""
        for l in picture["lines"]:
            if self.label_match({"lines": [l]}):
                # the label's own text block only: other text in the picture is the chart's ...
                below = [o for o in picture["lines"] if o["block"] == l["block"] and o["bbox"][1] >= l["bbox"][1] - 1]
                if len(below) == 1:  # ... except a caption block opening with a bold title
                    below += self.bold_caption([o for o in picture["lines"] if o["block"] != l["block"]])
                return self.acfg["_label"].match(l["text"].strip()), {"lines": below, "role": "caption"}
        return None

    @staticmethod
    def bold_caption(lines):
        """The lines of the largest multi-line text block opening with a bold title (a figure's title and
        caption set among, or beside, the chart's own labels); [] when there is none."""
        blocks = {}
        for o in lines:
            blocks.setdefault(o["block"], []).append(o)
        caps = [ls for ls in blocks.values() if len(ls) >= 2 and ls[0]["markup"].startswith("<b>")]
        if not caps:
            return []
        return sorted(max(caps, key=lambda ls: sum(len(o["text"]) for o in ls)),
                      key=lambda o: (o["bbox"][1], o["bbox"][0]))

    def caption_beside(self, lab, area, near, free, used):
        """A label that found no title or caption: take a bold-led caption block from its picture or from
        a region right next to it (a picture or body region holding only the caption). Returns the
        label as a pseudo region carrying those lines, or the label unchanged."""
        cands = near + sorted((c for c in free if id(c) not in used and c is not lab and c not in near
                               and c["role"] in ("picture", "body", "caption", "margin")
                               and self.vgap(c["bbox"], area) < 14 and self.hgap(c["bbox"], area) < 14),
                              key=lambda c: self.vgap(c["bbox"], area) + self.hgap(c["bbox"], area))
        for c in cands:
            cap = self.bold_caption(c["lines"])
            if len(" ".join(o["text"] for o in cap).split()) >= 10:  # prose, not a table's bold header row
                if c not in near:
                    used.add(id(c))  # its text now lives in the caption
                return {**lab, "lines": lab["lines"] + cap, "role": "caption"}, c
        return lab, None

    def bare_label(self, lab):
        """A figure label region holding nothing but the label ("FIGURE 6-11"): no title, no caption.
        (Tables are left alone: the bold text inside them is their header row, not a caption.)"""
        m = self.acfg["_label"].match(first_line(lab)["text"].strip())
        return len(lab["lines"]) == 1 and m and not m.group(1).lower().startswith("tab") \
            and not first_line(lab)["text"].strip()[m.end():].strip(" .:|—–-")

    def group(self, pno, pg):
        """Make figure, photo and box units out of panels, labels, pictures and captions."""
        regions = pg["regions"]
        units = []
        used = set()
        # panels: a figure when it holds a label, else a box
        for p in pg["panels"]:
            members = [m for m in p["members"] if m["role"] != "skip" and "lab_box" not in m
                       and not (m.get("labelled") and m["role"] not in ("picture", "caption", "body", "heading", "list"))]
            if not members:
                continue
            members.sort(key=lambda m: (m["bbox"].y0, m["bbox"].x0))
            labels = [m for m in members if m["role"] != "picture" and self.label_match(m)]
            inners = [x for x in (self.inner_label(m) for m in members if m["role"] == "picture") if x]
            # running text in body type: the panel is a box even if it shows a figure or two
            prose = sum(len(m["lines"]) for m in members if m["role"] in ("body", "list") and m not in labels
                        and first_line(m)["size"] >= 0.95 * self.style["size"])
            # ... or much more text than a caption, whatever its size (an FYI box with a small figure)
            words = sum(len(region_text(m)) for m in members if m["role"] not in ("picture",) and m not in labels)
            # ... unless it is a table of text: one Table label, no picture, little body-type prose (a long
            # list of propositions in small type is still Mankiw's Table 1, not a box)
            text_table = (words > 1200 and len(labels) == 1 and not inners and prose <= 8
                          and self.label_match(labels[0]).group(1).lower().startswith("tab")
                          and not any(m["role"] == "picture" for m in members))
            if (len(labels) + len(inners) == 1 and prose <= 8 and words <= 1200) or text_table:
                lab = labels[0] if labels else {**inners[0][1], "bbox": p["rect"]}
                pics = [m["bbox"] for m in members if m["role"] == "picture"]
                outside = [m for m in members if m is not lab and m["role"] != "picture" and m["lines"]
                           and not any(pb.contains((m["bbox"].tl + m["bbox"].br) / 2) for pb in pics)]
                if self.bare_label(lab) and not outside:  # title and caption beside the chart, maybe inside it
                    lab, src = self.caption_beside(lab, p["rect"], [m for m in members if m["role"] == "picture"],
                                                   [m for m in members if m is not lab], set())
                    used.update(id(m) for m in members)  # the label and the caption's region are taken
                    members = [lab if labels and m is labels[0] else m for m in members
                               if m is not src or m["role"] == "picture"]
                if labels:
                    units.append(dict(self.figure_unit(lab, members, p["rect"]), text_table=text_table))
                else:
                    units.append({"kind": "figure", "regions": members, "rect": pymupdf.Rect(p["rect"]),
                                  "label_region": lab, "word": inners[0][0].group(1),
                                  "num": inners[0][0].group(2)})
                used.update(id(m) for m in members)
                continue
            if not any(m["role"] in ("body", "list", "heading") for m in members):
                continue
            keep = members
            if labels or inners:  # the figures inside are found by the label pass below
                for lab in labels:  # a caption running on into a region of its own goes with its label
                    for m in members:
                        if m["role"] == "body" and len(m["lines"]) <= 2 and 0 <= m["bbox"].y0 - lab["bbox"].y1 < 6 \
                                and self.hgap(m["bbox"], lab["bbox"]) < 4:
                            m["role"] = "caption"
                keep = [m for m in members if m not in labels and m["role"] not in ("picture", "caption")]
            units.append({"kind": "box", "regions": keep, "rect": p["rect"]})
            used.update(id(m) for m in keep)
        # labels outside panels: the label region plus the nearest picture/table above or below it
        free = [r for r in regions if id(r) not in used and r["role"] != "skip" and "lab_box" not in r]
        pics = [r for r in free if r["role"] == "picture" and not r.get("lab_label")]
        labelled_pics = [r for r in free if r["role"] == "picture" and r.get("lab_label")]
        for r in free:
            if r["role"] == "picture" or id(r) in used or not self.label_match(r):
                continue
            near = sorted((pc for pc in pics if id(pc) not in used and self.hgap(pc["bbox"], r["bbox"]) <= 60
                           and self.vgap(pc["bbox"], r["bbox"]) <= 60),
                          key=lambda pc: self.vgap(pc["bbox"], r["bbox"]) + self.hgap(pc["bbox"], r["bbox"]))
            pos = self.acfg["caption_position"]
            if pos in ("below", "above"):  # the caption sits under (over) its picture: prefer that one
                side = [pc for pc in near if (pc["bbox"].y1 <= r["bbox"].y0 + 5 if pos == "below"
                                              else pc["bbox"].y0 >= r["bbox"].y1 - 5)]
                near = side or near
            if not near and r["role"] != "caption":
                continue
            members = [r] + near[:1]
            # a title or explanation set as separate caption (or margin) regions under the label
            chain = pymupdf.Rect(r["bbox"])
            for c in sorted(free, key=lambda c: c["bbox"].y0):
                if c is not r and id(c) not in used and c["role"] in ("caption", "margin") \
                        and 0 <= c["bbox"].y0 - chain.y1 < 14 and self.hgap(c["bbox"], chain) < 4:
                    members.append(c)
                    chain |= c["bbox"]
            area = pymupdf.Rect(chain)
            for m in near[:1]:
                area |= m["bbox"]
            if len(members) == 1 + len(near[:1]) and self.bare_label(r):  # a bare label: caption beside it?
                members[0] = self.caption_beside(r, area, near[:1], free, used)[0]
                used.add(id(r))
            units.append(self.figure_unit(members[0], members, area))
            used.update(id(m) for m in members)
        # hand labels: {box: NAME} gathers regions into one box (across pages too);
        # {label: "Figure 3"} makes a picture a numbered figure
        named = {}
        for r in regions:
            if "lab_box" in r and r["role"] != "skip":
                named.setdefault(r["lab_box"], []).append(r)
        for name, members in named.items():
            members.sort(key=lambda m: (m["bbox"].y0, m["bbox"].x0))
            rect = pymupdf.Rect(members[0]["bbox"])
            for m in members[1:]:
                rect |= m["bbox"]
            units.append({"kind": "box", "name": name, "regions": members, "rect": rect})
            used.update(id(m) for m in members)
        for pc in labelled_pics:
            if id(pc) not in used:
                m = self.acfg["_label"].match(pc["lab_label"])
                if not m:
                    raise SystemExit(f"labels: {pc['id']} label {pc['lab_label']!r} does not match auto.figure_label")
                units.append({"kind": "figure", "regions": [pc], "rect": pymupdf.Rect(pc["bbox"]), "label_region": None,
                              "word": m.group(1), "num": m.group(2)})
                used.add(id(pc))
        # a label printed inside the picture itself ("FIGURE A-2" among the chart's text)
        for pc in pics:
            inner = None if id(pc) in used else self.inner_label(pc)
            if inner:
                lab = inner[1]
                if self.bare_label(lab):  # only the label: the caption may be a region beside the chart
                    used.add(id(pc))
                    lab = self.caption_beside({**lab, "bbox": pc["bbox"]}, pc["bbox"], [], free, used)[0]
                units.append({"kind": "figure", "regions": [pc], "rect": pymupdf.Rect(pc["bbox"]),
                              "label_region": lab, "word": inner[0].group(1), "num": inner[0].group(2)})
                used.add(id(pc))
        # pictures with no label: photos (with an adjacent caption), tiny ones are ornaments
        for pc in pics:
            if id(pc) in used:
                continue
            used.add(id(pc))
            if (pc["bbox"].width < 40 or pc["bbox"].height < 30) and not pc["lines"]:
                continue  # an ornament (a one-row table, which carries text, is not)
            caps = [c for c in free if id(c) not in used and c["role"] in ("caption", "margin", "body")
                    and self.vgap(c["bbox"], pc["bbox"]) < 14 and self.hgap(c["bbox"], pc["bbox"]) < 4
                    and len(c["lines"]) <= 4 and (c["role"] in ("caption", "margin")
                                                  or first_line(c)["size"] < self.style["size"])]
            if not caps:  # a short heading right above it is its title ("Firstbank's Balance Sheet" over a table)
                caps = [c for c in free if id(c) not in used and c["role"] == "heading" and not c.get("toc")
                        and 0 <= pc["bbox"].y0 - c["bbox"].y1 < 14 and self.hgap(c["bbox"], pc["bbox"]) < 4
                        and len(c["lines"]) <= 2][:1]
            if (not caps and self.acfg["uncaptioned_pictures"] == "skip" and not pc.get("lab_keep")
                    and not pc.get("lab_continue")):
                self.note(pc, "picture without a caption: left out")
                continue
            for c in caps:
                used.add(id(c))
            units.append({"kind": "photo", "regions": [pc] + caps, "rect": pc["bbox"], "picture": pc,
                          "captions": caps, "margin": pc["margin"]})
        for r in regions:
            if id(r) in used:
                r["unit"] = True
        pg["units"] = units
        if any(r["role"] == "caption" and id(r) not in used for r in regions):
            for r in regions:
                if r["role"] == "caption" and id(r) not in used:
                    self.note(r, "caption with no picture: kept as body text")
                    r["role"] = "body"

    @staticmethod
    def vgap(a, b):
        return max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))

    @staticmethod
    def hgap(a, b):
        return max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))

    def figure_unit(self, lab, members, rect):
        m = self.label_match(lab)
        return {"kind": "figure", "regions": members, "rect": pymupdf.Rect(rect), "label_region": lab,
                "word": m.group(1), "num": m.group(2)}

    # ------------------------------------------------------------ reading order
    def order(self, pno, pg):
        units = list(pg["units"])
        for u in units:
            if u["kind"] == "photo" and u["margin"]:
                u["aside"] = True
        for r in pg["regions"]:
            if r.get("unit") or r["role"] in ("skip", "picture"):
                continue
            if r["role"] == "margin":
                continue
            units.append({"kind": "region", "regions": [r], "rect": pymupdf.Rect(r["bbox"])})
        main = [u for u in units if not u.get("aside")]
        ordered = xy_cut(main)
        keyed = []
        for k, u in enumerate(ordered):
            lab = next((r["lab_order"] for r in u["regions"] if "lab_order" in r), None)
            keyed.append((lab if lab is not None else k + 1, k, u))
        keyed.sort(key=lambda t: (t[0], t[1]))
        pg["ordered"] = [u for _, _, u in keyed]
        # {after: p56.r7}: read this unit right after the unit holding that region (ids do not shift)
        for u in [u for u in pg["ordered"] if any("lab_after" in r for r in u["regions"])]:
            target = next(r["lab_after"] for r in u["regions"] if "lab_after" in r)
            host = next((x for x in pg["ordered"] if x is not u and any(r["id"] == target for r in x["regions"])), None)
            if host is None:
                raise SystemExit(f"labels: after: {target} is not a region read on page {pno + 1}")
            pg["ordered"].remove(u)
            pg["ordered"].insert(pg["ordered"].index(host) + 1, u)
        for n, u in enumerate(pg["ordered"]):
            for r in u["regions"]:
                r["order"] = n + 1
        pg["margins"] = sorted([r for r in pg["regions"] if r["role"] == "margin" and not r.get("unit")],
                               key=lambda r: r["bbox"].y0)
        pg["asides"] = [u for u in units if u.get("aside")]

    # ------------------------------------------------------------ dump for overlay.py and review
    def dump(self):
        pages = {}
        for pno, pg in self.pages.items():
            unit_of = {}
            for k, u in enumerate(pg["units"]):
                for r in u["regions"]:
                    unit_of[r["id"]] = f"{u['kind']}{k + 1}"
            pages[pno + 1] = [{"id": r["id"], "bbox": [round(v, 1) for v in r["bbox"]], "cls": r["cls"],
                               "role": r["role"], "level": r.get("level"), "order": r.get("order"),
                               "unit": unit_of.get(r["id"]), "labelled": bool(r.get("labelled")),
                               "text": region_text(r)[:90]}
                              for r in pg["regions"]]
            pages[pno + 1].extend({"id": f"p{pno + 1}.panel{k + 1}", "bbox": [round(v, 1) for v in p["rect"]],
                                   "cls": "panel", "role": "panel", "text": ""}
                                  for k, p in enumerate(pg["panels"]))
        return pages


def xy_cut(units):
    """Reading order of page units: split at a vertical gutter first, then at a horizontal gap."""
    if len(units) <= 1:
        return list(units)
    by_x = sorted(units, key=lambda u: u["rect"].x0)
    reach = by_x[0]["rect"].x1
    for k in range(1, len(by_x)):
        if by_x[k]["rect"].x0 - reach >= 6:
            return xy_cut(by_x[:k]) + xy_cut(by_x[k:])
        reach = max(reach, by_x[k]["rect"].x1)
    by_y = sorted(units, key=lambda u: (u["rect"].y0, u["rect"].x0))
    reach = by_y[0]["rect"].y1
    for k in range(1, len(by_y)):
        if by_y[k]["rect"].y0 >= reach - 1:
            return xy_cut(by_y[:k]) + xy_cut(by_y[k:])
        reach = max(reach, by_y[k]["rect"].y1)
    return by_y


def chapter_ranges(doc, pattern, title=""):
    """[(number, title, first_index, last_index, sub_entries)] from the PDF outline.

    The first outline entry with a number wins (an answer key may repeat "Chapter 1" ...).
    A PDF without an outline, or an empty pattern, is one chapter: an article or a paper."""
    toc = doc.get_toc()
    if not pattern or not toc:
        subs = [(l, t.replace("\x00", "").strip()) for l, t, _ in toc]
        return [(1, title or (doc.metadata or {}).get("title") or "", 0, doc.page_count - 1, subs)]
    pat = re.compile(pattern)
    chapters, seen = [], set()
    for i, (lvl, title, page) in enumerate(toc):
        title = title.replace("\x00", "").replace("\r", " ")
        m = pat.match(title)
        if not m or int(m.group(1)) in seen:
            continue
        seen.add(int(m.group(1)))
        j = next((k for k in range(i + 1, len(toc)) if toc[k][0] <= lvl), len(toc))
        nxt = toc[j][2] if j < len(toc) else doc.page_count + 1
        subs = [(l - lvl, t.replace("\x00", "").replace("\r", " ").strip()) for l, t, _ in toc[i + 1:j]]
        chapters.append((int(m.group(1)), re.sub(r"\s+", " ", m.group(2)).strip(), page - 1, nxt - 2, subs))
    return chapters
