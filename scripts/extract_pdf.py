"""PDF source: rebuild a chapter's reading order and structure from fonts and positions.

Everything specific to one book (fonts, sizes, page geometry, label words) comes from the
profile's layout / figures / quick_quiz / experts / margin_glossary / end_matter / appendix
sections; see references/profile.md. The engine knows the usual furniture of a textbook:
headings, body text with drop caps, margin glossary notes, labelled side boxes (including boxes
that spill across pages), Quick-Quiz style review questions, labelled figures and tables,
photos with captions, displayed quotations, numbered and bulleted lists, an expert-poll panel,
end-of-chapter material and an appendix. Features a profile does not configure stay off.
"""
import re
from collections import Counter

import pymupdf

from bookkit import as_list, rule_matches
from chapterkit import ChapterBase, extract_each
from textkit import Joiner, clean_markup, span_markup, split_sentences, strip_tags

CFG = {}  # the book profile, set by configure()


def configure(book):
    CFG.clear()
    CFG.update(book.cfg)
    lay = CFG.setdefault("layout", {})
    lay.setdefault("roles", [])
    lay.setdefault("bold_markers", ["Bold", "Bd", "Black"])
    lay.setdefault("italic_markers", ["Italic", "Obl"])
    lay.setdefault("heading_number", r"(\d+-\d+[a-z]?)\s+(.*)")
    lay.setdefault("page_offset", 0)
    lay["_symbol_map"] = str.maketrans(lay.get("symbol_map") or {})
    fig = CFG.setdefault("figures", {})
    fig["_label"] = re.compile(fig["label_pattern"]) if fig.get("label_pattern") else None
    for k in ("captions", "quick_quiz", "experts", "margin_glossary", "end_matter", "appendix"):
        CFG.setdefault(k, {})


def layout(key, default=None):
    v = CFG["layout"].get(key)
    return default if v is None else v


def has_font(line, key):
    """Substring test against a profile font marker such as layout.box_font ("Cn18")."""
    marker = layout(key)
    return bool(marker) and marker in line["font"]


# ---------------------------------------------------------------- helpers


def chapter_ranges(doc):
    """[(number, title, first_index, last_index)] from the PDF outline."""
    pat = re.compile(CFG["chapters"]["outline_pattern"])
    toc = doc.get_toc()
    chapters = []
    for i, (lvl, title, page) in enumerate(toc):
        m = pat.match(title)
        if not m:
            continue
        nxt = next((p for (l, _, p) in toc[i + 1:] if l <= lvl), doc.page_count + 1)
        chapters.append((int(m.group(1)), re.sub(r"\s+", " ", m.group(2)).strip(), page - 1, nxt - 2))
    return chapters


def build_vocab(doc):
    """Words that occur in the book, used to decide how to undo line-end hyphens."""
    vocab = Counter()
    for page in doc:
        for w in re.findall(r"[A-Za-z][A-Za-z\-]*[A-Za-z]", page.get_text()):
            vocab[w.lower()] += 1
    return vocab


# ---------------------------------------------------------------- line model


def page_geometry(printed):
    pages = layout("pages", {})
    return pages.get("even" if printed % 2 == 0 else "odd") or pages.get("all") or {}


def page_lines(page, pno):
    """Flatten a page into lines with font info, in content-stream order."""
    printed = pno - layout("page_offset")
    geo = page_geometry(printed)
    main_left = geo.get("main_left", 0)
    symbol_fonts = tuple(as_list(layout("symbol_fonts", [])))
    bold, italic = layout("bold_markers"), layout("italic_markers")
    body_font, end_mark, quote_marker = layout("body_font", ""), layout("end_mark_font"), layout("box_quote_marker")
    lines = []
    d = page.get_text("dict")
    for bi, b in enumerate(d["blocks"]):
        if b["type"] != 0:
            lines.append({"kind": "image", "bbox": b["bbox"], "page": pno, "block": bi})
            continue
        block_lines = []
        for l in b["lines"]:
            spans = [s for s in l["spans"] if s["text"]]
            for s in spans:
                if symbol_fonts and s["font"].startswith(symbol_fonts):
                    s["text"] = s["text"].translate(layout("_symbol_map"))
            if not spans:
                continue
            fonts = Counter()
            for s in spans:
                fonts[(s["font"], round(s["size"], 1))] += len(s["text"].strip())
            (font, size), _ = fonts.most_common(1)[0]
            text = "".join(s["text"] for s in spans)
            if not text.strip():
                continue
            block_lines.append({
                "kind": "text", "text": text, "markup": "".join(span_markup(s, bold, italic) for s in spans),
                "font": font, "size": size, "bbox": l["bbox"], "block_bbox": b["bbox"],
                "page": pno, "printed": printed, "block": bi, "main_left": main_left, "geo": geo, "spans": spans,
                "endmark": bool(end_mark) and any(end_mark in sp["font"] for sp in spans) and len(text.strip()) > 2,
                "all_obl": bool(quote_marker) and all(quote_marker in sp["font"] for sp in spans if sp["text"].strip()),
            })
        # a block of body text indented on every line is a displayed quotation
        offsets = [l["bbox"][0] - main_left for l in block_lines]
        bq = (len(block_lines) >= 2 and all(9 <= o <= 20 for o in offsets)
              and all(l["font"].startswith(body_font) for l in block_lines)
              and not block_lines[0]["text"].lstrip().startswith("•"))
        # quotations in boxes are set entirely in oblique; a single oblique line is just a book title
        obl = len(block_lines) >= 2 and all(l["all_obl"] for l in block_lines)
        for l in block_lines:
            l["bq"] = bq
            l["block_obl"] = obl
            l["block_n"] = len(block_lines)
        lines.extend(block_lines)
    return lines


def in_margin(line):
    if line.get("no_margin"):
        return False
    m = line["geo"].get("margin")
    if not m:
        return False
    x0, _, x1, _ = line["bbox"]
    if "x1_max" in m and x1 > m["x1_max"]:
        return False
    if "x0_min" in m and x0 < m["x0_min"]:
        return False
    return True


EXTRA = {"in_margin": in_margin}


def matches(line, rule):
    return rule_matches(line, rule, EXTRA)


def classify_line(line):
    """Role of a line from the profile's ordered layout.roles table; the first match wins."""
    if line.get("force_caption"):
        return "caption"
    for rule in CFG["layout"]["roles"]:
        if matches(line, rule):
            return rule["role"]
    return "body"


BOX_FLOW = {"news_intro", "news_headline", "news_byline"}


def figure_region(page_images, label_bbox, page):
    """Area of a figure/table: the tinted panel that contains its label."""
    lab = pymupdf.Rect(label_bbox)
    containing = [pymupdf.Rect(im["bbox"]) for im in page_images if pymupdf.Rect(im["bbox"]).contains(lab)]
    if containing:
        return min(containing, key=lambda r: r.get_area())
    region = pymupdf.Rect(lab)
    for dr in page.get_drawings():
        r = dr["rect"]
        if r.y0 >= lab.y0 - 10 and r.y0 - region.y1 < 30 and r.width < page.rect.width:
            region |= r
    return region


def order_page(lines, page):
    """Put a page's lines in reading order and pull out figures and side boxes.

    Content-stream order is unreliable (headings and box labels often come last),
    so main-column lines are sorted by position. Box text set in the box font is read
    column by column and anchored where the box starts. Figures, tables and expert-poll
    panels become single items carrying their text.
    """
    images = [l for l in lines if l["kind"] == "image"]
    text = []
    for l in lines:  # glue multi-line labels such as CASE / STUDY
        if l["kind"] != "text":
            continue
        if text and classify_line(l) == "label" and classify_line(text[-1]) == "label" \
                and text[-1]["block"] == l["block"]:
            m = text[-1]
            m["text"] = m["text"].strip() + " " + l["text"].strip()
            m["markup"] = m["text"]
            m["bbox"] = tuple(pymupdf.Rect(m["bbox"]) | pymupdf.Rect(l["bbox"]))
            continue
        if text and classify_line(text[-1]) == "news_byline" and text[-1]["block"] == l["block"] \
                and l["font"] == text[-1]["font"]:  # a byline that wraps ("By A and / B")
            m = text[-1]
            m["text"] = m["text"].rstrip() + " " + l["text"].strip()
            m["markup"] = m["markup"].rstrip() + " " + l["markup"].strip()
            m["bbox"] = tuple(pymupdf.Rect(m["bbox"]) | pymupdf.Rect(l["bbox"]))
            continue
        text.append(dict(l))

    # a short stand-alone line set just under a picture is its caption, whatever its font
    pictures = [im["bbox"] for im in images
                if im["bbox"][2] - im["bbox"][0] < 300 and im["bbox"][3] - im["bbox"][1] > 40]
    photo_italic = CFG["captions"].get("photo_italic_font")
    for l in text:
        x0, y0 = l["bbox"][0], l["bbox"][1]
        if len(l["text"].strip()) < 80 and any(
                0 <= y0 - b[3] <= 12 and b[0] - 4 <= x0 <= b[2]
                # a short block of its own, or an italic line lying wholly within the picture's width
                and (l.get("block_n", 9) <= 2 or ("Italic" in l["font"] and l["bbox"][2] <= b[2] + 4))
                for b in pictures):
            l["force_caption"] = True
        # under a wide photo only an italic line standing alone in its block is trusted to be a caption
        elif photo_italic and len(l["text"].strip()) < 80 and l["font"].startswith(photo_italic) \
                and l.get("block_n", 9) <= 2 and any(
                0 <= y0 - im["bbox"][3] <= 12 and im["bbox"][0] - 4 <= x0 <= im["bbox"][2]
                and im["bbox"][3] - im["bbox"][1] > 40 for im in images):
            l["force_caption"] = True
    # body text that merely resumes under a picture runs straight on into the next block in the same font
    for l in text:
        if l.get("force_caption"):
            bottom = max(m["bbox"][3] for m in text if m["block"] == l["block"])
            if any(m["block"] != l["block"] and m["font"] == l["font"] and m["size"] == l["size"]
                   and 0 <= m["bbox"][1] - bottom <= 3 and abs(m["bbox"][0] - l["bbox"][0]) <= 15 for m in text):
                del l["force_caption"]
    for k in range(1, len(text)):  # a two-line caption: the second line sits further below the picture
        l, prev = text[k], text[k - 1]
        if prev.get("force_caption") and l["block"] == prev["block"] and l["font"] == prev["font"] \
                and 0 <= l["bbox"][1] - prev["bbox"][3] <= 3:
            l["force_caption"] = True

    # the end-of-chapter material may start on the conclusion's page; its short lines are not margin notes
    banner_rule = CFG["end_matter"].get("banner")
    banner = next((l["bbox"][1] for l in text if matches(l, banner_rule)), None) if banner_rule else None
    if banner is not None:
        for l in text:
            if l["bbox"][1] > banner:
                l["no_margin"] = True

    taken = set()
    fig_label = CFG["figures"]["_label"]
    experts_label = CFG["experts"].get("label")
    for l in text:
        label = re.sub(r"\s+", " ", l["text"].strip().upper())
        if classify_line(l) != "label":
            continue
        if fig_label and fig_label.match(label):
            region = figure_region(images, l["bbox"], page)
            members = [o for o in text if o is not l and pymupdf.Rect(o["bbox"]).intersects(region)
                       and region.contains(pymupdf.Point((o["bbox"][0] + o["bbox"][2]) / 2,
                                                         (o["bbox"][1] + o["bbox"][3]) / 2))]
            l["figure"] = {"region": tuple(region), "lines": members}
            taken.update(id(o) for o in members)
        elif experts_label and label == experts_label.upper():
            header = [pymupdf.Rect(im["bbox"]) for im in images
                      if abs(im["bbox"][1] - l["bbox"][1]) < 15 and im["bbox"][0] <= l["bbox"][0]]
            x1 = max((r.x1 for r in header), default=l["bbox"][0] + 220) + 8
            src = [o for o in text if classify_line(o) == "source" and o["bbox"][1] > l["bbox"][1]
                   and o["bbox"][0] < x1]
            y1 = min((o["bbox"][3] for o in src), default=l["bbox"][1] + 260) + 2
            region = pymupdf.Rect(l["bbox"][0] - 15, l["bbox"][1] - 4, x1, y1)
            members = [o for o in text if o is not l and region.contains(pymupdf.Rect(o["bbox"]))]
            l["experts"] = {"region": tuple(region), "lines": members}
            taken.update(id(o) for o in members)

    main, box, margin = [], [], []
    for l in text:
        if id(l) in taken:
            continue
        k = classify_line(l)
        if k == "skip":
            continue
        if k == "margin_def" or (k == "caption" and not has_font(l, "box_font")):
            margin.append(l)
        elif has_font(l, "box_font") or k in BOX_FLOW:
            box.append(l)
        else:
            main.append(l)
    # labels ("Quick Quiz", "FYI") sit beside or just below the first line they introduce
    main.sort(key=lambda l: (round(l["bbox"][1]) - (8 if classify_line(l) == "label" else 0), l["bbox"][0]))
    if box:
        # read multi-column boxes column by column: cluster the blocks' left edges
        lefts = sorted({round(l["block_bbox"][0]) for l in box})
        cols = []
        for x in lefts:
            if cols and x - cols[-1][-1] < 30:
                cols[-1].append(x)
            else:
                cols.append([x])

        def col_of(l):
            x = round(l["block_bbox"][0])
            return next(i for i, c in enumerate(cols) if x in c)
        box.sort(key=lambda l: (col_of(l), l["bbox"][1]))
        anchor = min(l["bbox"][1] for l in box)
        cut = next((i for i, l in enumerate(main) if l["bbox"][1] > anchor), len(main))
        main = main[:cut] + box + main[cut:]
    margin.sort(key=lambda l: l["bbox"][1])
    return main + margin + images


# ---------------------------------------------------------------- assembly


class PdfChapter(ChapterBase):
    def __init__(self, book, doc, chap, vocab):
        num, title, self.first, self.last = chap
        super().__init__(book, num, title)
        self.doc = doc
        self.join = Joiner(vocab).join
        self.margin_buf = []
        self.caption_buf = []
        off = layout("page_offset")
        self.pages = [self.first - off, self.last - off]

    # ------------------------------------------------------------ running text
    def make_para(self, lines, kind, dropcap=None):
        html = self.join([l["markup"] for l in lines])
        if dropcap:
            html = dropcap + html.lstrip()
        html = re.sub(r"^([a-z]+):", lambda m: m.group(1).capitalize() + ":", html)  # small-caps speaker names
        blk = {"type": kind, "id": "", "page": lines[0]["printed"], "pdf_page": lines[0]["page"],
               "y": lines[0]["bbox"][1], "sentences": []}
        if html.lstrip().startswith("•"):
            blk["type"] = "bullets"
            parts = [x.strip() for x in re.split(r"\s*•\s*", html) if x.strip()]
        else:
            parts = split_sentences(html)
        blk["sentences"] = [{"id": "", "html": s} for s in parts]
        blk["terms"] = [t.strip(" ,.;:") for t in re.findall(r"<b>([^<]+)</b>", html)]
        return blk

    def parse(self, lines):
        """Turn ordered lines into blocks. Figures and boxes never cut a paragraph in two:
        they are held back and placed after the paragraph that was running when they appeared."""
        blocks = []
        st = {"para": [], "dropcap": None, "box": None, "saved": None, "pending": [], "mode": "body",
              "prev": None, "dropcap_box": None, "pending_title": None, "suspended": None, "last_box": None}
        qq_label = CFG["quick_quiz"].get("label")
        qq_key = re.sub(r"\s+", "", qq_label.upper()) if qq_label else None
        qq_strip = re.compile(r"^\s*" + r"\s*".join(map(re.escape, qq_label.split())) + r"\s*") if qq_label else None
        qq_continue = CFG["quick_quiz"].get("continue")
        heading_num = re.compile(layout("heading_number"))
        body_font = layout("body_font", "")

        def target():
            return st["box"]["blocks"] if st["box"] else blocks

        def flush():
            if st["para"]:
                kind = "quote" if st["para"][-1].get("_quote") else "para"
                target().append(self.make_para(st["para"], kind, st["dropcap"]))
                st["para"], st["dropcap"] = [], None
            if not st["box"] and st["pending"]:
                blocks.extend(st["pending"])
                st["pending"] = []

        def close_box():
            flush()
            box = st["box"]
            st["box"] = None
            st["para"], st["dropcap"] = st["saved"] or ([], None)
            st["saved"] = None
            st["pending"].append(box)
            st["last_box"] = box
            if not st["para"]:
                flush()

        # a box that spills onto the next page is interrupted by main text:
        # park it, let the main text continue, and pick it up again when its text reappears
        def suspend_box():
            st["suspended"] = (st["box"], st["para"], st["dropcap"])
            st["box"] = None
            st["para"], st["dropcap"] = st["saved"] or ([], None)
            st["saved"] = None

        def resume_box():
            st["saved"] = (st["para"], st["dropcap"])
            st["box"], st["para"], st["dropcap"] = st["suspended"]
            st["suspended"] = None

        def finish_quiz():
            qtext = self.join([l["markup"] for l in st["para"]])
            items = [x.strip() for x in re.split(r"\s*•\s*", strip_tags(qtext)) if x.strip()]
            quiz = next(b for b in reversed(blocks) if b["type"] == "quickquiz")
            quiz["items"] = [{"id": f"{quiz['id']}.{k + 1}", "html": x} for k, x in enumerate(items)]
            st["para"], st["mode"] = [], "body"

        for line in lines:
            if line["kind"] == "image":
                continue
            kind = classify_line(line)
            text = line["text"].strip()
            if kind in ("skip", "chapter_title"):
                continue
            if st["mode"] == "quickquiz" and kind != "quiz":
                # quiz items may mix oblique and upright type ("Define microeconomics ...")
                if qq_continue and matches(line, qq_continue) and not in_margin(line) and kind not in ("label",):
                    kind = "quiz"
                else:
                    finish_quiz()
            if kind == "dropcap":
                st["dropcap"] = text
                st["dropcap_box"] = (line["page"], line["bbox"][3], line["bbox"][2])
                continue
            if kind == "margin_def":
                self.margin_buf.append(line)
                continue
            if kind == "caption":
                self.caption_buf.append(line)
                continue
            if kind == "source":
                if st["box"] is not None:
                    st["box"]["source"] = strip_tags(self.join([line["markup"]]))
                continue
            if kind in ("news_intro", "news_headline", "news_byline") and st["box"] is not None:
                key = {"news_intro": "intro", "news_headline": "headline", "news_byline": "byline"}[kind]
                st["box"][key] = self.join([st["box"].get(key, ""), strip_tags(line["markup"])])
                st["prev"] = line
                continue
            if kind in ("h1", "h2"):
                if st["box"] and st["box"].get("cn18"):
                    suspend_box()
                elif st["box"]:
                    close_box()
                flush()
                level = 1 if kind == "h1" else 2
                prev = st["prev"]
                if prev is not None and prev.get("_kind") == kind and blocks and blocks[-1]["type"] == "heading":
                    blocks[-1]["text"] = clean_markup(blocks[-1]["text"] + " " + text)
                else:
                    m = heading_num.match(text)
                    num = m.group(1) if m else ""
                    hid = "h" + num if num else "hA" + self.new_id("hA")[2:].lstrip("0")
                    blocks.append({"type": "heading", "level": level, "num": num, "id": hid,
                                   "text": m.group(2) if m else text})
                line["_kind"] = kind
                st["prev"] = line
                continue
            if kind == "label":
                up = re.sub(r"\s+", " ", text.upper())
                if qq_key and up.replace(" ", "").startswith(qq_key):
                    flush()
                    blocks.append({"type": "quickquiz", "id": self.new_id("qq"), "page": line["printed"], "items": []})
                    st["mode"] = "quickquiz"
                    rest = qq_strip.sub("", text)
                    st["para"] = [dict(line, markup=rest)] if rest else []
                    st["prev"] = line
                    continue
                if "figure" in line:
                    st["pending"].append(self.figure_block(line))
                    continue
                if "experts" in line:
                    st["pending"].append(self.experts_block(line))
                    continue
                # FYI / Case Study / In the News: the running paragraph resumes after the box
                if st["box"]:
                    close_box()
                if st["suspended"]:
                    resume_box()
                    close_box()
                st["saved"] = (st["para"], st["dropcap"])
                st["para"], st["dropcap"] = [], None
                pt = st["pending_title"]
                st["box"] = {"type": "box", "id": self.new_id("box"), "label": text,
                             "title": pt[1] if pt and pt[0] == line["page"] else "", "blocks": [],
                             "page": line["printed"]}
                st["pending_title"] = None
                line["_kind"] = "label"
                st["prev"] = line
                continue
            if kind == "box_title":
                if st["box"] is not None:
                    st["box"]["title"] = clean_markup((st["box"]["title"] + " " + text).strip())
                else:
                    st["pending_title"] = (line["page"], text)
                st["prev"] = line
                continue
            if kind == "quiz" and st["mode"] != "quickquiz":
                kind = "body"

            # ---- running text (body, quiz, box body)
            cn18 = has_font(line, "box_font")  # set in the side-box font
            if st["box"] and st["box"].get("cn18") and body_font and line["font"].startswith(body_font):
                suspend_box()
            elif not st["box"] and st["suspended"] and cn18:
                resume_box()
            elif not st["box"] and cn18 and st["last_box"] and line["printed"] - st["last_box"]["page"] <= 1:
                # author note printed under a box ("Ms Oster is a professor of ...")
                lb = st["last_box"]
                lb["author"] = self.join([lb.get("author", ""), strip_tags(line["markup"])])
                continue
            is_quote = line.get("bq") or (st["box"] is not None and cn18 and line.get("block_obl"))
            dc = st["dropcap_box"]
            wrapped = dc and dc[0] == line["page"] and line["bbox"][1] < dc[1] - 8 and line["bbox"][0] <= dc[2] + 20
            indent = 0 if wrapped else line["bbox"][0] - line["block_bbox"][0]
            # numbered steps ("1. The hot weather ...") are set with a hanging indent
            starts_item = st["mode"] != "quickquiz" and re.match(r"\s*\d{1,2}\.\s", line["text"])  # not "1974. Then"
            first = st["para"][0] if st["para"] else None
            in_item = first is not None and re.match(r"\s*\d{1,2}\.\s", first["text"])
            hang = line["bbox"][0] - line["main_left"] - (first["bbox"][0] - first["main_left"]) if first else 0
            if in_item and not starts_item and 15 <= hang <= 24:
                is_quote = first.get("_quote")
                line["_quote"] = is_quote
                st["para"].append(line)
                st["prev"] = line
                continue
            if st["para"]:
                last = st["para"][-1]
                # a list item ends when the text returns to the left margin
                new_para = bool(starts_item) or bool(in_item and abs(hang) <= 3)
                in_bullets = st["para"][0]["text"].lstrip().startswith("•")
                if st["mode"] != "quickquiz":
                    if 6 < indent < 20 and not in_bullets:
                        new_para = True
                    if line["page"] == last["page"] and line["bbox"][1] - last["bbox"][3] > 6 \
                            and abs(line["block_bbox"][0] - last["block_bbox"][0]) < 30:
                        new_para = True
                    if bool(last.get("_quote")) != bool(is_quote):
                        new_para = True
                    if line["text"].lstrip().startswith("•") and not in_bullets:
                        new_para = True
                    # a bullet's continuation lines are set in from the bullet; text that is not ends the list
                    lead = st["para"][0]
                    if in_bullets and not line["text"].lstrip().startswith("•") and \
                            (line["bbox"][0] - line["main_left"]) - (lead["bbox"][0] - lead["main_left"]) < 9:
                        new_para = True
                    if re.match(r"[a-z]+:\s", line["text"].lstrip()) and 15 < indent < 50:
                        new_para = True  # small-caps speaker label opening a dialogue turn
                if new_para:
                    flush()
            line["_quote"] = is_quote
            if st["box"] is not None and "cn18" not in st["box"]:
                st["box"]["cn18"] = cn18
            st["para"].append(line)
            st["prev"] = line
            if line.get("endmark") and st["box"]:
                close_box()
        if st["mode"] == "quickquiz":
            finish_quiz()
        if st["suspended"]:
            if st["box"]:
                close_box()
            resume_box()
        if st["box"]:
            close_box()
        flush()
        blocks.extend(st["pending"])
        return blocks

    # ------------------------------------------------------------ figures and side panels
    def figure_block(self, line):
        fig, fcfg = line["figure"], CFG["figures"]
        label = re.sub(r"\s+", " ", line["text"].strip())
        m = fcfg["_label"].match(label.upper())
        kind, num = m.group(1).lower(), m.group(2)
        members = fig["lines"]
        title = [o for o in members if matches(o, fcfg.get("title"))]
        # the caption is the longest flush-left run of small text; numbers in a table drawn
        # inside the figure use the same font but are scattered across columns
        small = sorted((o for o in members if matches(o, fcfg.get("caption"))), key=lambda o: o["bbox"][1])
        runs = []
        for o in small:
            run = next((r for r in runs if abs(r[-1]["bbox"][0] - o["bbox"][0]) <= 3
                        and -2 <= o["bbox"][1] - r[-1]["bbox"][3] <= 6), None)
            if run:
                run.append(o)
            else:
                runs.append([o])
        caption = max(runs, key=lambda r: sum(len(o["text"]) for o in r)) if runs else []
        rows = [o for o in members if matches(o, fcfg.get("rows"))]
        source = [o for o in members if matches(o, fcfg.get("source"))]
        bid = f"{kind[:3]}{num}"
        fname = f"{self.cid}-{bid}.png"
        self.save_clip(line["page"], fig["region"], fname)
        blk = {"type": kind, "id": bid, "label": f"{kind.title()} {num}", "page": line["printed"],
               "title": self.join([strip_tags(o["markup"]) for o in title]),
               "caption": strip_tags(self.join([o["markup"] for o in caption])), "src": f"img/{fname}"}
        joined = strip_tags(self.join([o["markup"] for o in sorted(rows, key=lambda o: o["bbox"][1])]))
        items = re.split(r"(?:^|\s)(\d{1,2})\.\s+", joined)
        if len(items) > 6:  # numbered rows, e.g. propositions in a table
            blk["rows"] = [items[i + 1].strip() for i in range(1, len(items) - 1, 2)]
        if source:
            blk["source"] = strip_tags(self.join([o["markup"] for o in source]))
        return blk

    def experts_block(self, line):
        ecfg = CFG["experts"]
        members = line["experts"]["lines"]
        title = [o for o in members if classify_line(o) == "box_title"]
        prop = [o for o in members if matches(o, ecfg.get("proposition"))]
        source = [o for o in members if classify_line(o) == "source"]
        # a panel may ask several questions, each followed by its own pie chart
        groups = []
        for o in sorted(prop, key=lambda o: o["bbox"][1]):
            if groups and o["bbox"][1] - groups[-1][-1]["bbox"][3] < 12:
                groups[-1].append(o)
            else:
                groups.append([o])
        region = pymupdf.Rect(line["experts"]["region"])
        pct_font = ecfg.get("percent_font")
        pcts = [o for o in self.page_cache[line["page"]]  # percentages are printed inside the pie charts
                if o["kind"] == "text" and pct_font and o["font"].startswith(pct_font)
                and region.contains(pymupdf.Rect(o["bbox"]))]
        items = []
        for k, g in enumerate(groups):
            top = g[0]["bbox"][1]
            bottom = groups[k + 1][0]["bbox"][1] if k + 1 < len(groups) else region.y1
            results = {}
            # a label may wrap, with the word on the line below its percentage ("43%" / "agree")
            here = sorted((o for o in pcts if top <= o["bbox"][1] <= bottom), key=lambda o: o["bbox"][1])
            texts = [o["text"].strip() for o in here]
            for j, o in enumerate(here):
                if re.fullmatch(r"\d+%", texts[j]):
                    word = next((texts[i] for i, p in enumerate(here) if i != j and 0 < p["bbox"][1] - o["bbox"][1] < 14
                                 and abs(p["bbox"][0] - o["bbox"][0]) < 10 and texts[i].isalpha()), "")
                    texts[j] += " " + word
            for t in texts:
                mm = re.match(r"(\d+)%\s*(agree|disagree|uncertain)", t, re.I)
                if mm:
                    results[mm.group(2).lower()] = int(mm.group(1))
            items.append({"proposition": strip_tags(self.join([o["markup"] for o in g])), "results": results})
        return {"type": "experts", "id": self.new_id("ask"), "page": line["printed"],
                "title": self.join([o["text"] for o in title]), "items": items,
                "source": strip_tags(self.join([o["markup"] for o in source]))}

    def save_clip(self, pno, bbox, fname, zoom=2.5):
        page = self.doc[pno]
        rect = pymupdf.Rect(bbox) & page.rect
        pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom))
        self.book.img_dir.mkdir(parents=True, exist_ok=True)
        pix.save(self.book.img_dir / fname)

    # ------------------------------------------------------------ whole chapter
    def run(self):
        lines = []
        self.page_cache = {}
        for pno in range(self.first, self.last + 1):
            raw = page_lines(self.doc[pno], pno)
            self.page_cache[pno] = raw
            lines.extend(order_page(raw, self.doc[pno]))

        def find(pred, start=0):
            return next((k for k in range(start, len(lines)) if lines[k]["kind"] == "text" and pred(lines[k])), None)

        # end-of-chapter material starts at its banner (or the fallback heading such as SUMMARY);
        # an appendix, if any, starts with its title after it
        em = CFG["end_matter"]
        end = find(lambda l: matches(l, em["banner"])) if em.get("banner") else None
        if end is None and em.get("fallback_start"):
            end = find(lambda l: l["text"].strip().upper().startswith(em["fallback_start"].upper()))
        end = end if end is not None else len(lines)
        app_rule = CFG["appendix"].get("title")
        appx = find(lambda l: matches(l, app_rule), end) if app_rule else None

        self.blocks = self.parse(lines[:end])
        self.appendix = self.parse(lines[appx:]) if appx is not None else []
        self.apply_patches()
        self.renumber()
        self.place_figures()
        self.attach_margin()
        self.attach_captions(lines)
        self.end_matter(lines[end:appx] if appx is not None else lines[end:])

    # --- margin definitions -> glossary, attached to the paragraph that bolds the term
    def attach_margin(self):
        marker = CFG["margin_glossary"].get("term_marker", "Bd")
        groups = []
        for l in self.margin_buf:
            starts = marker in l["spans"][0]["font"]
            prev = groups[-1][-1] if groups else None
            close = prev is not None and l["page"] == prev["page"] and l["bbox"][1] - prev["bbox"][3] <= 6
            prev_all_term = prev is not None and all(marker in s["font"] for s in prev["spans"])
            if close and (not starts or prev_all_term):
                groups[-1].append(l)
            else:
                groups.append([l])
        paras = list(self.iter_paras())
        for g in groups:
            term = re.sub(r"\s+", " ", " ".join(s["text"] for l in g for s in l["spans"] if marker in s["font"])).strip()
            definition = self.join(["".join(s["text"] for s in l["spans"] if marker not in s["font"]) for l in g])
            if not term:
                print(f"  ! margin text without a term on p.{g[0]['printed']}: {definition[:60]}")
                continue
            entry = {"term": term, "def": strip_tags(definition), "page": g[0]["printed"]}
            host = next((p for p in paras if any(t.lower() == term.lower() for t in p.get("terms", []))), None)
            if host is None:
                same_page = [p for p in paras if p.get("pdf_page") == g[0]["page"] and p["y"] <= g[0]["bbox"][1] + 40]
                host = same_page[-1] if same_page else None
            entry["para"] = host["id"] if host else None
            self.glossary.append(entry)

    # --- photos / cartoons with their captions
    def attach_captions(self, lines):
        groups = []
        for l in self.caption_buf:
            prev = groups[-1][-1] if groups else None
            # lines that follow each other, or speech captions side by side under a strip cartoon
            if prev and l["page"] == prev["page"] and (0 <= l["bbox"][1] - prev["bbox"][3] < 8
                                                        or abs(l["bbox"][1] - prev["bbox"][1]) < 4):
                groups[-1].append(l)
            else:
                groups.append([l])
        images = [l for l in lines if l["kind"] == "image"]
        paras = list(self.iter_paras())
        for g in groups:
            cap = strip_tags(self.join([l["markup"] for l in g]))
            pno = g[0]["page"]
            cy = g[0]["bbox"][1]
            cands = [im for im in images if im["page"] == pno and im["bbox"][3] <= cy + 4
                     and (im["bbox"][2] - im["bbox"][0]) < 400]
            if not cands:
                continue
            im = min(cands, key=lambda im: cy - im["bbox"][3])
            region = pymupdf.Rect(im["bbox"])
            for other in cands:  # the other panels of a strip cartoon sit on the same row
                if abs(other["bbox"][3] - im["bbox"][3]) < 4 and other["bbox"][3] - other["bbox"][1] > 40:
                    region |= pymupdf.Rect(other["bbox"])
            pid = self.new_id("img")
            fname = f"{self.cid}-{pid}.png"
            self.save_clip(pno, tuple(region), fname)
            host = [p for p in paras if p.get("pdf_page") == pno and p["y"] <= im["bbox"][1] + 60]
            host = host[-1] if host else next((p for p in paras if p.get("pdf_page", 0) >= pno), None)
            self.photos.append({"id": pid, "src": f"img/{fname}", "caption": cap,
                                "para": host["id"] if host else None})

    # --- end-of-chapter material (multiple choice quiz, summary)
    def end_matter(self, rest):
        self.endmatter = {}
        rest = [l for l in rest if l["kind"] == "text" and classify_line(l) != "skip"]
        sections_cfg = CFG["end_matter"].get("sections", [])
        headers = []
        for l in rest:
            t = l["text"].strip().upper()
            if classify_line(l) == "label":
                key = next((s["key"] for s in sections_cfg
                            if ("contains" in s and s["contains"].upper() in t)
                            or ("starts" in s and t.startswith(s["starts"].upper()))), None)
                if key:
                    headers.append((l["page"], l["bbox"][1], key))
        headers.sort()

        def section_of(l):
            k = None
            for (p, y, key) in headers:
                if (p, y) <= (l["page"], l["bbox"][1]):
                    k = key
            return k

        sections = {}
        for l in rest:
            if classify_line(l) == "label":
                continue
            k = section_of(l)
            if k:
                sections.setdefault(k, []).append(l)
        for k, ls in sections.items():
            ls.sort(key=lambda l: (l["page"], 0 if l["bbox"][0] < 300 else 1, l["bbox"][1]))
            sections[k] = self.join([l["markup"] for l in ls])

        if "quiz" in sections:
            # question numbers run 1, 2, 3...; a number inside a stem ("demanded is 100. If") is not one
            text, starts, n = sections["quiz"], [], 1
            for m in re.finditer(r"(?:^|\s)(\d{1,2})\.\s(?=[A-Z])", text):
                if int(m.group(1)) == n:
                    starts.append((n, m.start(), m.end()))
                    n += 1
            qs = []
            for k, (num, _, body) in enumerate(starts):
                stem = text[body:starts[k + 1][1] if k + 1 < len(starts) else len(text)]
                parts = re.split(r"\s([a-d])\.\s", " " + stem)
                opts = {parts[j]: strip_tags(parts[j + 1].strip()) for j in range(1, len(parts) - 1, 2)}
                qs.append({"id": f"cq{num}", "stem": strip_tags(parts[0].strip()), "options": opts})
            self.endmatter["quiz"] = qs
        if "summary" in sections:
            items = [x.strip() for x in re.split(r"\s*•\s*", sections["summary"]) if x.strip()]
            self.endmatter["summary"] = [{"id": f"sum{k + 1}", "html": x} for k, x in enumerate(items)]


def extract(book, nums):
    configure(book)
    doc = pymupdf.open(book.source)
    chapters = {c[0]: c for c in chapter_ranges(doc)}
    if not chapters:
        raise SystemExit("no chapters found: check chapters.outline_pattern against the PDF outline "
                         "(font_profile.py prints it)")
    vocab = build_vocab(doc)
    def extract_one(num):
        if num not in chapters:
            raise SystemExit(f"chapter {num} not in the outline (found {sorted(chapters)})")
        ch = PdfChapter(book, doc, chapters[num], vocab)
        ch.run()
        ch.write()
    extract_each(nums or sorted(chapters), extract_one)
