"""Automatic PDF engine: chapters from the layout model's regions, with hand labels for the rest.

pdf_layout.PageSet gives every page's regions with roles and reading order; this module turns them
into the chapter structure every engine produces (headings, paragraphs and sentences, boxes, figures,
photos, margin glossary) and writes, next to build/source/chNN.*, the region map for review:

    build/layout.json         cached model output (region ids p37.r5 never shift)
    build/layout/chNN.json    every region's role, order and unit, for overlay.py
    build/layout/chNN.txt     the same as a list, plus "review" notes worth a look

Corrections, in order of preference: a hand label in labels/chNN.yaml (role, order, join, box ... of a
region; references/auto-layout.md), a region rule in book.yaml `auto.rules` when the same fix recurs,
a text patch in patches/chNN.json for wording the PDF encodes badly.
"""
import json
import re
from collections import Counter

import pymupdf
import yaml

from chapterkit import ChapterBase, extract_each
from pdf_layout import (BULLETS, LIGATURES, LayoutCache, PageSet, auto_config, body_style, chapter_ranges, first_line, norm,
                        same_title,
                        region_text, xy_cut)
from textkit import Joiner, split_sentences, strip_tags


def expand_labels(labels):
    """`p44.r2-r11: {...}` labels regions r2 to r11 of page 44 alike; later keys win."""
    out = {}
    for key, val in labels.items():
        m = re.fullmatch(r"p(\d+)\.r(\d+)-r?(\d+)", str(key))
        if m:
            for k in range(int(m.group(2)), int(m.group(3)) + 1):
                old = out.get(f"p{m.group(1)}.r{k}")
                out[f"p{m.group(1)}.r{k}"] = {**old, **val} if isinstance(old, dict) and isinstance(val, dict) else val
        else:
            old = out.get(str(key))
            out[str(key)] = {**old, **val} if isinstance(old, dict) and isinstance(val, dict) else val
    return out


END_MARKS = ("■", "▪", "◼", "●", "◆")  # printed at the end of a box


def ended(markup):
    return re.search(r"[.?!:;]\W*$", strip_tags(markup).rstrip()) is not None


class AutoChapter(ChapterBase):
    def __init__(self, book, doc, chap, vocab, cache, acfg, style):
        num, title, self.first, self.last, subs = chap
        super().__init__(book, num, title)
        self.doc, self.acfg = doc, acfg
        self.join = Joiner(vocab).join
        path = book.root / "labels" / f"{self.cid}.yaml"
        labels = expand_labels(yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
        self.ps = PageSet(book, doc, cache, self.first, self.last, title, subs, acfg, labels, style)
        self.pages = [self.printed(self.first), self.printed(self.last)]
        self.paras = []        # every paragraph block in reading order, with its page and y
        self.boxes_by_name = {}
        self.hosts = []        # (entry, host paragraph block) resolved after renumbering
        self.side_notes = []   # (box block, host paragraph block)

    def printed(self, pno):
        label = self.doc[pno].get_label()
        if label and label.isdigit():
            return int(label)
        off = self.acfg["page_offset"]
        return pno - off if off is not None else pno + 1

    # ------------------------------------------------------------ paragraphs
    def chunks(self, region):
        """Split a region's lines into paragraphs: bullets, first-line indents, wider gaps."""
        ls = region["lines"]
        if not ls:
            return []
        x0 = min(l["bbox"][0] for l in ls)
        gaps = sorted(b["bbox"][1] - a["bbox"][3] for a, b in zip(ls, ls[1:]))
        gap = gaps[len(gaps) // 2] if gaps else 2
        out = [[ls[0]]]
        for l in ls[1:]:
            prev, lead = out[-1][-1], out[-1][0]
            t = l["text"].lstrip()
            ind, prev_ind = l["bbox"][0] - x0, prev["bbox"][0] - x0
            # a list item or a dialogue turn: hanging indent, the next one starts back at the left
            hanging = len(out[-1]) > 1 and out[-1][1]["bbox"][0] - lead["bbox"][0] >= 6
            item = hanging or lead["text"].lstrip()[:1] in BULLETS or re.match(r"\d{1,2}[.)]\s", lead["text"].lstrip())
            if t[:1] in BULLETS or (region["role"] == "list" and re.match(r"\d{1,2}[.)]\s", t)):
                new = True
            elif item:
                new = l["bbox"][0] <= lead["bbox"][0] + 8 and prev["bbox"][0] > lead["bbox"][0] + 6 \
                    and ended(prev["markup"])
            else:
                new = 6 <= ind <= 40 and prev_ind <= 2 and ended(prev["markup"])
            if l["bbox"][1] - prev["bbox"][3] > max(4, 1.8 * gap) and ended(prev["markup"]):
                new = True
            if new:
                out.append([l])
            else:
                out[-1].append(l)
        return out

    def make_para(self, lines, kind, region, dropcap=None):
        html = self.join([l["markup"] for l in lines])
        if dropcap:
            html = dropcap + html.lstrip()
        blk = {"type": kind, "id": "", "page": self.printed(region["pno"]), "pdf_page": region["pno"],
               "y": lines[0]["bbox"][1], "sentences": []}
        if strip_tags(html).lstrip()[:1] in BULLETS:
            blk["type"] = "bullets"
            parts = [x.strip() for x in re.split(r"\s*[" + BULLETS + r"]\s*", html) if strip_tags(x).strip()]
        else:
            parts = split_sentences(html)
        blk["sentences"] = [{"id": "", "html": s} for s in parts]
        blk["terms"] = [t.strip(" ,.;:") for t in re.findall(r"<b>([^<]+)</b>", html)]
        return blk

    # ------------------------------------------------------------ flow of one list of blocks
    class Flow:
        """Paragraph assembly into one block list (the chapter or a box). Figures and boxes that
        appear while a paragraph is still open wait until it ends, so they never cut it in two."""

        def __init__(self, ch, target):
            self.ch, self.target = ch, target
            self.cur = None  # [lines, kind, region, dropcap]
            self.pending = []
            self.waiting = []  # photos met while the paragraph was open: they belong to it

        def close(self):
            if self.cur:
                lines, kind, region, dc = self.cur
                blk = self.ch.make_para(lines, kind, region, dc)
                if self.target and self.target[-1]["type"] == "bullets" and blk["type"] == "bullets" \
                        and self.target[-1].get("_open"):
                    self.target[-1]["sentences"].extend(blk["sentences"])
                    self.target[-1]["terms"].extend(blk["terms"])
                else:
                    self.target.append(blk)
                    self.ch.paras.append(blk)
                blk["_open"] = blk["type"] == "bullets"
                self.ch.hosts.extend((e, blk) for e in self.waiting)
                self.waiting = []
                self.cur = None
            self.target.extend(self.pending)
            self.pending = []

        def add(self, blk):
            if self.target and self.target[-1].get("_open"):
                self.target[-1]["_open"] = False
            if self.cur:
                self.pending.append(blk)
            else:
                self.target.append(blk)

        def text(self, region):
            kind = "quote" if region["role"] == "quote" else "para"
            for k, chunk in enumerate(self.ch.chunks(region)):
                dc = region.get("dropcap") if k == 0 else None
                if k == 0 and self.joins(region, chunk, kind):
                    self.cur[0].extend(chunk)
                    continue
                if self.cur or self.pending:
                    self.close()
                if self.target and self.target[-1].get("_open") and chunk[0]["text"].lstrip()[:1] not in BULLETS:
                    self.target[-1]["_open"] = False
                self.cur = [list(chunk), kind, region, dc]

        def joins(self, region, chunk, kind):
            if region.get("lab_join") is not None:
                return bool(region["lab_join"]) and self.cur is not None
            if self.cur is None or self.cur[1] != kind or region.get("dropcap"):
                return False
            first = chunk[0]["text"].lstrip()
            if not first or first[:1] in BULLETS:
                return False
            if not ended(self.cur[0][-1]["markup"]):
                return True
            return first[0].islower()

        def heading(self, blk):
            self.close()
            self.target.append(blk)

    # ------------------------------------------------------------ units
    def heading_block(self, r):
        text = self.join([l["markup"] for l in r["lines"]])
        plain = strip_tags(text).strip()
        # a section number drawn apart from the title may land inside it, or twice ("3- What … 3-1 of …"):
        # keep the fullest number token, put it first and drop the fragments
        frag = r"(?<![\w-])\d+(?:[.\-]\d*)+[a-z]?(?![\w-])"
        tokens = re.findall(frag, plain)
        if tokens and not (len(tokens) == 1 and plain.startswith(tokens[0] + " ")):
            rest = re.sub(r"\s+", " ", re.sub(frag, " ", plain)).strip()
            whole = [t.rstrip(".-") for t in tokens if re.fullmatch(r"\d+(?:[.\-]\d+)+[a-z]?", t.rstrip(".-"))]
            # the number whose outline entry has this title wins; else the fullest, the last of equals
            known = [t for t in whole if any(same_title(norm(t + rest), norm(title)) for _, title in self.ps.toc)]
            full = (known or sorted(whole, key=len)[-1:] or [""])[0]
            if full:
                plain = full + " " + rest
        m = self.acfg["_hnum"].match(plain)
        num = m.group(1).rstrip(".-") if m else ""
        base = "h" + num.replace(".", "-") if num else "hA" + self.new_id("hA")[2:].lstrip("0")
        hid, k = base, 1
        while hid in self.heading_ids:
            k += 1
            hid = f"{base}-{k}"
        self.heading_ids.add(hid)
        return {"type": "heading", "level": r.get("level") or 2, "num": num, "id": hid,
                "text": m.group(2) if m else plain}

    def figure_block(self, u):
        word, num = u["word"], u["num"]
        kind = "table" if word.lower().startswith("tab") else "figure"
        label = f"{word.title() if word.isupper() else word} {num}"
        pics = [m["bbox"] for m in u["regions"] if m["role"] == "picture"]
        lab = u["label_region"]
        texts = sorted((m for m in u["regions"] if m is not lab and m["role"] != "picture" and m["lines"]
                        and not any(p.contains(pymupdf.Point((m["bbox"].x0 + m["bbox"].x1) / 2,
                                                             (m["bbox"].y0 + m["bbox"].y1) / 2)) for p in pics)),
                       key=lambda m: (m["bbox"].y0, m["bbox"].x0))
        rest, title = "", ""
        if lab is not None:
            # chart text merged above the label ("Grade" | "FIGURE A-2 ..."): the caption starts at the label
            k = next((k for k, l in enumerate(lab["lines"]) if self.acfg["_label"].match(l["text"].strip())), 0)
            lab = dict(lab, lines=lab["lines"][k:]) if k else lab
            m = self.acfg["_label"].match(first_line(lab)["text"].strip())
            first = first_line(lab)["text"].strip()[m.end():].lstrip(" .:|—–-")
            rest = strip_tags(self.join([first] + [l["markup"] for l in lab["lines"][1:]]))
            title, rest = self.split_title(lab, m.end(), rest)
        heads = [m for m in texts if m["role"] == "heading"]
        if heads and not title:
            title = strip_tags(self.join([l["markup"] for l in heads[0]["lines"]]))
            texts.remove(heads[0])
        caption_parts = [rest] if rest else []
        source = []
        for m in texts:
            t = strip_tags(self.join([l["markup"] for l in m["lines"]]))
            if not title and not caption_parts:  # a caption opening with its title in other type
                title, t = self.split_title(m, 0, t)
            (source if re.match(r"(Source|Sources|Note|Data)\b", t) else caption_parts).append(t)
        caption = " ".join(caption_parts).strip()
        if not title and caption:
            sents = split_sentences(caption)
            title, caption = sents[0], " ".join(sents[1:])
        bid = f"{kind[:3]}{re.sub(r'[^0-9A-Za-z]+', '-', num)}"
        while bid in self.figure_ids:
            bid += "b"
        self.figure_ids.add(bid)
        fname = f"{self.cid}-{bid}.png"
        pno = u["regions"][0]["pno"]
        self.save_clip(pno, u["rect"], fname)
        self.figure_clips[bid] = (fname, [(pno, pymupdf.Rect(u["rect"]))])
        blk = {"type": kind, "id": bid, "label": label, "page": self.printed(pno),
               "title": title, "caption": caption, "src": f"img/{fname}"}
        if source:
            blk["source"] = " ".join(source)
        return blk

    @staticmethod
    def split_title(lab, skip, rest):
        """A title set in its own font after the label ("FIGURE 1.2 Scarcity of Resources People ..."):
        the run of spans in the first font after the first `skip` characters (the label), when different
        type follows. Returns (title, rest without it), or ("", rest)."""
        spans = [s for l in lab["lines"] for s in l["spans"] if s["text"].strip()]
        label = re.sub(r"\s", "", (lab["lines"][0]["text"] if lab["lines"] else "").strip()[:skip])
        taken, k = "", 0
        while k < len(spans) and len(taken) < len(label):  # the spans that make up the label (spaces aside:
            taken += re.sub(r"\s", "", spans[k]["text"])      #  "FIGURE" + "3-2" may come without one)
            k += 1
        # the label's last span may carry the start of the title ("Figure 2-2 Stocks and Flows")
        tail, extra = "", len(taken) - len(label)
        if k and extra > 0:  # walk back over the last span until `extra` non-space characters are passed
            last, cut = spans[k - 1]["text"], len(spans[k - 1]["text"])
            while extra and cut:
                cut -= 1
                extra -= 0 if last[cut].isspace() else 1
            tail = last[cut:].strip(" .:|—–-")
        run = [dict(spans[k - 1], text=tail)] if tail and k else []
        while k < len(spans) and (not run or spans[k]["font"] == run[0]["font"]):
            run.append(spans[k])
            k += 1
        title = re.sub(r"\s+", " ", " ".join(s["text"] for s in run)).strip(" .:|—–-")
        if run and k < len(spans) and title and len(title) <= 120 and rest.startswith(title):
            return title, rest[len(title):].strip(" .:")
        return "", rest

    def photo(self, u, host):
        pc = u["picture"]
        if pc.get("lab_continue"):  # the rest of a figure or table carried over to a later page
            if pc["lab_continue"] not in self.figure_clips:
                raise SystemExit(f"labels: {pc['id']} continues {pc['lab_continue']}, which is not an earlier"
                                 " figure or table of this chapter")
            fname, clips = self.figure_clips[pc["lab_continue"]]
            clips.append((pc["pno"], pymupdf.Rect(pc["bbox"])))
            self.save_stack(fname, clips)
            return
        cap = strip_tags(self.join([l["markup"] for c in u["captions"] for l in c["lines"]]))
        pid = self.new_id("img")
        fname = f"{self.cid}-{pid}.png"
        self.save_clip(pc["pno"], pc["bbox"], fname)
        entry = {"id": pid, "src": f"img/{fname}", "caption": cap, "para": None}
        self.photos.append(entry)
        if isinstance(host, self.Flow):
            host.waiting.append(entry)
        else:
            self.hosts.append((entry, host))

    def box_block(self, u, flow, into=None, cont=False):
        """A box unit becomes a box block; `into`: an empty box opened by a heading (WORK IT OUT) that
        this panel fills, its first heading becoming the title; `cont`: the panel continues the last box."""
        regs = [m for m in u["regions"] if m["role"] not in ("picture", "skip")]
        order = xy_cut([{"rect": pymupdf.Rect(m["bbox"]), "regions": [m]} for m in regs])
        regs = [x["regions"][0] for x in order]
        cont = cont or any(m.get("lab_join") for m in regs)
        name = u.get("name")
        box = self.boxes_by_name.get(name) if name else None
        if into is not None:
            box = into
            if not box["title"] and regs and regs[0]["role"] == "heading":
                box["title"] = strip_tags(self.join([l["markup"] for l in regs.pop(0)["lines"]]))
        if box is None and cont and self.last_box is not None:
            box = self.last_box
        given = next((m["lab_label"] for m in regs if m.get("lab_label")), None)
        regs = [m for m in regs if not m.get("lab_label")]  # the region the hand label names is the label
        if box is None:
            box = {"type": "box", "id": self.new_id("box"), "label": given or "", "title": "", "blocks": [],
                   "page": self.printed(u["regions"][0]["pno"])}
            if not given and regs and len(region_text(regs[0])) <= self.acfg["box_label_max"] and len(regs) > 1 \
                    and regs[0]["role"] in ("heading", "margin", "caption", "body") \
                    and (regs[0]["role"] != "body" or first_line(regs[0])["bold"]):
                box["label"] = region_text(regs.pop(0))
            if regs and regs[0]["role"] == "heading":
                box["title"] = strip_tags(self.join([l["markup"] for l in regs.pop(0)["lines"]]))
            if not box["label"]:  # a box with a title only: show the title as its label
                box["label"], box["title"] = box["title"] or "Box", ""
            if name:
                self.boxes_by_name[name] = box
            flow.add(box)
            box["_flow"] = self.Flow(self, box["blocks"])
        self.last_box = box
        inner = box["_flow"]
        for m in regs:
            if m["role"] == "heading":
                inner.heading(self.heading_block(m))
            elif m["role"] == "formula":
                inner.close()
                inner.add(self.math_block(m))
            else:
                if m["role"] in ("margin", "caption"):
                    m = dict(m, role="body")
                inner.text(m)
        for p in u["regions"]:
            if p["role"] == "picture" and p["bbox"].width >= 40 and p["bbox"].height >= 30:
                self.photo({"picture": p, "captions": []}, None)

    def math_block(self, r):
        return {"type": "math", "id": self.new_id("eq"), "page": self.printed(r["pno"]),
                "html": self.join([l["markup"] for l in r["lines"]])}

    def save_clip(self, pno, bbox, fname, zoom=2.5):
        page = self.doc[pno]
        rect = pymupdf.Rect(bbox) & page.rect
        pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom))
        self.book.img_dir.mkdir(parents=True, exist_ok=True)
        pix.save(self.book.img_dir / fname)

    def save_stack(self, fname, clips, zoom=2.5):
        """A figure continued on later pages: its parts stacked top to bottom in one image."""
        rects = [r & self.doc[p].rect for p, r in clips]
        tmp = pymupdf.open()
        page = tmp.new_page(width=max(r.width for r in rects), height=sum(r.height for r in rects))
        y = 0
        for (p, _), r in zip(clips, rects):
            page.show_pdf_page(pymupdf.Rect(0, y, r.width, y + r.height), self.doc, p, clip=r)
            y += r.height
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(self.book.img_dir / fname)

    # ------------------------------------------------------------ margins
    def margin_notes(self, pno, margins):
        bold = lambda s: any(m in s["font"] for m in self.acfg["bold_markers"]) or s["flags"] & 16
        # line by line: one region may hold several notes; a bold start after plain text opens a new one
        groups = []
        for r in margins:
            for l in sorted(r["lines"], key=lambda l: l["bbox"][1]):
                starts_bold = bold(next(s for s in l["spans"] if s["text"].strip()))
                prev = groups[-1][-1][1] if groups else None
                if prev is not None and l["bbox"][1] - prev["bbox"][3] <= 8 \
                        and not (starts_bold and not prev["bold"]):
                    groups[-1].append((r, l))
                else:
                    groups.append([(r, l)])
        page_paras = [p for p in self.paras if p["pdf_page"] == pno]
        for g in groups:
            lines = [l for _, l in g]
            y = lines[0]["bbox"][1]
            g = [r for r, _ in g]
            spans = [s for l in lines for s in l["spans"] if s["text"].strip()]
            term = ""
            if spans and bold(spans[0]):
                k = 0
                while k < len(spans) and bold(spans[k]):
                    k += 1
                term = re.sub(r"\s+", " ", " ".join(s["text"] for s in spans[:k])).strip(" :")
                rest = self.join([s["text"] for s in spans[k:]])
            near = [p for p in page_paras if p["y"] <= y + 40]
            if term and rest:
                host = next((p for p in self.paras if any(t.lower() == term.lower() for t in p.get("terms", []))),
                            None) or (near[-1] if near else (page_paras[0] if page_paras else None))
                entry = {"term": term, "def": strip_tags(rest), "page": self.printed(pno), "para": None}
                self.glossary.append(entry)
                self.hosts.append((entry, host))
            else:
                host = near[-1] if near else (page_paras[0] if page_paras else (self.paras[-1] if self.paras else None))
                para = self.make_para(lines, "para", g[0])
                box = {"type": "box", "id": self.new_id("note"), "label": "Note", "title": "", "blocks": [para],
                       "page": self.printed(pno)}
                self.side_notes.append((box, host))
                self.ps.note(g[0], "margin text without a bold term: kept as a side note")

    # ------------------------------------------------------------ whole chapter
    def run(self):
        self.heading_ids, self.figure_ids = set(), set()
        self.figure_clips = {}  # figure id -> (image file, [(page, rect), ...]) for continued figures
        self.last_box = None
        margins = []
        flow = self.Flow(self, self.blocks)
        skip_names = {norm(s) for s in self.acfg["skip_sections"]}
        skipping = None  # level of a skipped section (References ...) until a heading as high
        box_heads = [re.compile(p, re.I) for p in self.acfg["box_headings"]]
        main, opened = flow, None  # opened: a box begun by a heading, running to a rule or outline heading
        runs_off = False
        for pno in range(self.first, self.last + 1):
            pg = self.ps.pages[pno]
            prev_y = 0
            # a rule with skipped small print just under it separates the footnotes; it ends no box
            rules = [y for y in pg["rules"] if not any(x["role"] == "skip" and 0 <= x["bbox"].y0 - y < 20
                                                       and x["lines"] for x in pg["regions"])]
            for u in pg["ordered"]:
                r = u["regions"][0]
                is_head = u["kind"] == "region" and r["role"] == "heading"
                if is_head:
                    level = r.get("level") or 2
                    if skipping is not None and level <= skipping:
                        skipping = None
                    m = self.acfg["_hnum"].match(region_text(r).strip())
                    if norm(m.group(2) if m else region_text(r)) in skip_names:
                        skipping = level
                if skipping is not None:
                    continue
                has_content = opened is not None and (opened["blocks"] or flow.cur or opened["title"])
                if opened is not None and ((has_content and any(prev_y - 1 <= y <= u["rect"].y0 + 1 for y in rules))
                                           or (is_head and r.get("toc") and has_content)):  # not its own title
                    flow.close()
                    flow, opened = main, None
                # a panel box at the top of a page continues a box that ran to the foot of the last page
                spill = prev_y == 0 and runs_off and u["kind"] == "box"
                prev_y = max(prev_y, u["rect"].y1)
                if u["kind"] == "box":  # nothing below it on the page
                    runs_off = u["rect"].y1 >= max(x["rect"].y1 for x in pg["ordered"]) - 2
                elif u["kind"] not in ("figure", "photo"):  # figures may sit inside the box's panel
                    runs_off = False
                if is_head and any(p.match(region_text(r).strip()) for p in box_heads):
                    main.close()
                    opened = {"type": "box", "id": self.new_id("box"), "label": region_text(r).strip(),
                              "title": "", "blocks": [], "page": self.printed(pno)}
                    main.target.append(opened)
                    flow = self.Flow(self, opened["blocks"])
                    continue
                if u["kind"] == "region":
                    if r["role"] == "heading" and opened is not None and not opened["title"] and not opened["blocks"] \
                            and flow.cur is None:
                        opened["title"] = strip_tags(self.join([l["markup"] for l in r["lines"]]))
                    elif r["role"] == "heading" and r.get("lab_join") and flow.target \
                            and flow.target[-1].get("type") == "heading":  # a heading's second line set apart
                        flow.target[-1]["text"] += " " + strip_tags(self.join([l["markup"] for l in r["lines"]])).strip()
                    elif r["role"] == "heading":
                        flow.heading(self.heading_block(r))
                    elif r["role"] in ("body", "list", "quote", "caption"):
                        flow.text(r)
                        if opened is not None and region_text(r).rstrip().endswith(END_MARKS):
                            flow.close()  # the end-of-box mark ("… easy to interpret. ■")
                            flow, opened = main, None
                    elif r["role"] == "formula":  # a displayed equation stays where it is set
                        flow.close()
                        flow.target.append(self.math_block(r))
                elif u["kind"] == "figure":
                    flow.add(self.figure_block(u))
                elif u["kind"] == "box" and opened is not None and not opened["blocks"] and flow.cur is None:
                    opened["_flow"] = flow  # the panel under the heading is the box's body, and its end
                    self.box_block(u, flow, into=opened)  # (its flow stays open for a spill onto the next page)
                    flow, opened = main, None
                elif u["kind"] == "box":
                    self.box_block(u, flow, cont=spill)
                elif u["kind"] == "photo":
                    self.photo(u, flow if flow.cur else (self.paras[-1] if self.paras else None))
            if opened is not None and (opened["blocks"] or flow.cur) and any(y >= prev_y - 1 for y in rules):
                flow.close()  # the rule closing the box is the last thing on the page
                flow, opened = main, None
            for u in pg["asides"]:
                near = [p for p in self.paras if p["pdf_page"] == pno and p["y"] <= u["rect"].y0 + 60]
                self.photo(u, near[-1] if near else (self.paras[-1] if self.paras else None))
            margins.append((pno, pg["margins"]))
        flow.close()
        main.close()
        for box in self.boxes_by_name.values():
            box["_flow"].close()
        for b in self.walk(self.blocks):
            if b["type"] == "box" and "_flow" in b:
                b["_flow"].close()
                del b["_flow"]
        for pno, ms in margins:
            self.margin_notes(pno, ms)
        for box, host in self.side_notes:
            self.insert_after(host, box)
        self.apply_patches()
        self.renumber()
        self.place_figures()
        for entry, host in self.hosts:
            entry["para"] = host["id"] if host else (self.paras[0]["id"] if self.paras else None)
        for b in self.walk(self.blocks):
            b.pop("_open", None)

    def walk(self, blocks):
        for b in blocks:
            yield b
            if b["type"] == "box":
                yield from self.walk(b["blocks"])

    def insert_after(self, host, blk):
        def find(lst):
            for i, b in enumerate(lst):
                if b is host:
                    return lst, i
                if b["type"] == "box":
                    hit = find(b["blocks"])
                    if hit:
                        return hit
            return None
        hit = find(self.blocks) if host is not None else None
        if hit:
            hit[0].insert(hit[1] + 1, blk)
        else:
            self.blocks.append(blk)

    # ------------------------------------------------------------ region map for review
    def write_layout(self):
        out = self.book.root / "build" / "layout"
        out.mkdir(parents=True, exist_ok=True)
        dump = self.ps.dump()
        notes = [{"page": p + 1, "region": rid, "note": msg} for p, rid, msg in self.ps.notes]
        (out / f"{self.cid}.json").write_text(json.dumps({"pages": dump, "notes": notes}, ensure_ascii=False),
                                              encoding="utf-8")
        lines = [f"# {self.cid} {self.title}: PDF pages {self.first + 1}-{self.last + 1}",
                 "# region  role(level)  order  unit  text   (* = hand label)"]
        if notes:
            lines.append("\n## review")
            lines.extend(f"  p{n['page']} {n['region']}: {n['note']}" for n in notes)
        for page, regions in dump.items():
            lines.append(f"\n## page {page}")
            for r in sorted(regions, key=lambda r: (r.get("order") or 999, r["bbox"][1])):
                if r["role"] == "panel":
                    continue
                role = r["role"] + (str(r["level"]) if r.get("level") and r["role"] == "heading" else "")
                lines.append(f"  {r['id']:<9} {role:<9} {r.get('order') or '-':>3}  {r.get('unit') or '':<8}"
                             f"{'*' if r.get('labelled') else ' '} {r['text']}")
        (out / f"{self.cid}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        counts = Counter(n["note"].split(":")[0] for n in notes)
        if counts:
            print("  review: " + "; ".join(f"{c}× {k}" for k, c in counts.most_common()))


def extract(book, nums):
    acfg = auto_config(book.cfg)
    doc = pymupdf.open(book.source)
    chapters = {c[0]: c for c in chapter_ranges(doc, book.cfg["chapters"]["outline_pattern"], book.cfg["book"]["title"])}
    if not chapters:
        raise SystemExit("no chapters found: check chapters.outline_pattern against the PDF outline")
    cache = LayoutCache(book, doc)
    # the book's words decide how to undo line-end hyphens (ligatures such as "ﬁ" spelt out)
    vocab = Counter(w.lower() for page in doc
                    for w in re.findall(r"[A-Za-z][A-Za-z\-]*[A-Za-z]", page.get_text().translate(LIGATURES)))
    sample = [p for c in chapters.values() for p in range(c[2], c[3] + 1)]
    style = body_style(doc, sample[:: max(1, len(sample) // 60)])
    def extract_one(num):
        if num not in chapters:
            raise SystemExit(f"chapter {num} not in the outline (found {sorted(chapters)})")
        ch = AutoChapter(book, doc, chapters[num], vocab, cache, acfg, style)
        ch.run()
        ch.write()
        ch.write_layout()
    try:
        extract_each(nums or sorted(chapters), extract_one)
    finally:
        cache.save()
