"""EPUB source: chapters come from the table of contents, structure from the HTML tags.

No layout inference is needed: headings, paragraphs, lists, quotations, figures, tables and
sidebars are read from the markup. The profile's `epub` section adjusts the mapping
(see references/profile.md); the defaults suit most trade and academic EPUBs.
"""
import posixpath
import re
import zipfile
from html import escape
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote

from chapterkit import ChapterBase
from textkit import clean_markup, split_sentences

EPUB_DEFAULTS = {
    "heading_map": {"h1": "title", "h2": 1, "h3": 2, "h4": 2, "h5": 2, "h6": 2},
    "box_tags": ["aside"],
    "box_classes": ["sidebar", "box", "boxed", "feature", "callout", "note-box"],
    "skip_classes": ["pagebreak", "page-break", "footnote", "footnotes", "endnote", "endnotes", "toc"],
    "skip_epub_types": ["footnote", "footnotes", "endnote", "endnotes", "rearnote", "pagebreak", "noteref", "toc"],
    "figure_label_pattern": r"^(Figure|Fig\.|Table|Exhibit)\s*([A-Z]?[\d.\-]+)[.:]?\s*",
    "heading_number": r"^((?:\d+[.\-])+\d*|\d+)\s+(.*)",
}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
INLINE_KEEP = {"b": "b", "strong": "b", "i": "i", "em": "i", "cite": "i", "dfn": "i", "var": "i",
               "sub": "sub", "sup": "sup"}
BLOCK_TAGS = {"p", "div", "section", "article", "blockquote", "ul", "ol", "li", "figure", "table", "pre",
              "h1", "h2", "h3", "h4", "h5", "h6", "aside", "header", "footer", "main", "body", "nav", "dl", "dt",
              "dd", "hr", "figcaption"}


class Node:
    def __init__(self, tag, attrs=None):
        self.tag, self.attrs, self.children = tag, attrs or {}, []

    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def epub_types(self):
        return set((self.attrs.get("epub:type") or "").split())

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def find(self, tag):
        for c in self.children:
            if isinstance(c, Node):
                if c.tag == tag:
                    return c
                hit = c.find(tag)
                if hit:
                    return hit
        return None


class TreeBuilder(HTMLParser):
    """A forgiving XHTML/HTML parser: EPUB files are meant to be XML but often are not quite."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(data):
    tb = TreeBuilder()
    tb.feed(data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data)
    return tb.root


# ---------------------------------------------------------------- package reading


class Package:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        container = parse_html(self.zip.read("META-INF/container.xml"))
        self.opf_path = container.find("rootfile").attrs["full-path"]
        opf = parse_html(self.zip.read(self.opf_path))
        base = posixpath.dirname(self.opf_path)
        self.manifest = {}
        nav_href = ncx_href = None
        for item in self._all(opf, "item"):
            href = posixpath.normpath(posixpath.join(base, unquote(item.attrs["href"])))
            self.manifest[item.attrs["id"]] = (href, item.attrs.get("media-type", ""))
            if "nav" in item.attrs.get("properties", "").split():
                nav_href = href
            if item.attrs.get("media-type") == "application/x-dtbncx+xml":
                ncx_href = href
        self.spine = [self.manifest[i.attrs["idref"]][0] for i in self._all(opf, "itemref")
                      if i.attrs.get("idref") in self.manifest]
        self.title = (opf.find("dc:title").text().strip() if opf.find("dc:title") else "")
        self.author = (opf.find("dc:creator").text().strip() if opf.find("dc:creator") else "")
        self.toc = self._nav_toc(nav_href) if nav_href else self._ncx_toc(ncx_href) if ncx_href else []

    @staticmethod
    def _all(node, tag, out=None):
        out = [] if out is None else out
        for c in node.children:
            if isinstance(c, Node):
                if c.tag == tag:
                    out.append(c)
                Package._all(c, tag, out)
        return out

    def _resolve(self, doc_path, href):
        f, _, frag = unquote(href).partition("#")
        path = posixpath.normpath(posixpath.join(posixpath.dirname(doc_path), f)) if f else doc_path
        return path, frag

    def _nav_toc(self, href):
        root = parse_html(self.zip.read(href))
        nav = next((n for n in self._all(root, "nav") if "toc" in n.epub_types()), None) or root.find("nav")
        entries = []

        def walk(ol, depth):
            for li in (c for c in ol.children if isinstance(c, Node) and c.tag == "li"):
                a = li.find("a")
                if a is not None and a.attrs.get("href"):
                    entries.append((re.sub(r"\s+", " ", a.text()).strip(), *self._resolve(href, a.attrs["href"]), depth))
                sub = next((c for c in li.children if isinstance(c, Node) and c.tag in ("ol", "ul")), None)
                if sub is not None:
                    walk(sub, depth + 1)
        top = nav.find("ol") or nav.find("ul") if nav else None
        if top is not None:
            walk(top, 1)
        return entries

    def _ncx_toc(self, href):
        root = parse_html(self.zip.read(href))
        entries = []

        def walk(node, depth):
            for c in node.children:
                if isinstance(c, Node) and c.tag == "navpoint":
                    label = c.find("text")
                    content = c.find("content")
                    if label is not None and content is not None:
                        entries.append((re.sub(r"\s+", " ", label.text()).strip(),
                                        *self._resolve(href, content.attrs.get("src", "")), depth))
                    walk(c, depth + 1)
        nav_map = root.find("navmap")
        if nav_map is not None:
            walk(nav_map, 1)
        return entries


# ---------------------------------------------------------------- linear event stream


class Linearizer:
    """Flatten the spine into block events so chapters can be cut at TOC anchors anywhere."""

    def __init__(self, pkg, cfg):
        self.pkg, self.cfg = pkg, cfg
        self.events = []
        self.anchor_pos = {}
        for path in pkg.spine:
            self.anchor_pos.setdefault((path, ""), len(self.events))
            root = parse_html(pkg.zip.read(path))
            body = root.find("body") or root
            self.doc = path
            self.walk(body, quote=False)

    def skipped(self, n):
        return (n.tag in ("script", "style", "nav", "head") or n.classes() & set(self.cfg["skip_classes"])
                or n.epub_types() & set(self.cfg["skip_epub_types"]))

    def is_box(self, n):
        return n.tag in self.cfg["box_tags"] or bool(n.classes() & set(self.cfg["box_classes"]))

    def mark(self, n):
        if n.attrs.get("id"):
            self.anchor_pos.setdefault((self.doc, n.attrs["id"]), len(self.events))

    def inline(self, n):
        """Inner HTML reduced to text with <b>/<i>/<sub>/<sup>."""
        out = []
        for c in n.children:
            if isinstance(c, str):
                out.append(escape(c, quote=False))  # a literal "a < b" must not read as a tag
                continue
            if self.skipped(c):
                continue
            self.mark(c)
            if c.tag == "br":
                out.append(" ")
            elif c.tag == "img":
                continue
            elif c.tag in INLINE_KEEP:
                t = INLINE_KEEP[c.tag]
                out.append(f"<{t}>{self.inline(c)}</{t}>")
            else:
                out.append(self.inline(c))
        s = "".join(out).replace("­", "").replace("\xa0", " ")
        s = re.sub(r"\s+", " ", s)
        return s

    def images_in(self, n):
        for c in n.children:
            if isinstance(c, Node):
                if c.tag == "img" and c.attrs.get("src"):
                    yield c
                elif c.tag in ("svg",):
                    img = c.find("image")
                    if img is not None and (img.attrs.get("xlink:href") or img.attrs.get("href")):
                        yield img
                elif not self.skipped(c):
                    yield from self.images_in(c)

    def img_path(self, img):
        src = img.attrs.get("src") or img.attrs.get("xlink:href") or img.attrs.get("href")
        return self.pkg._resolve(self.doc, src)[0]

    def emit(self, **ev):
        ev["doc"] = self.doc
        self.events.append(ev)

    def walk(self, n, quote):
        for c in n.children:
            if isinstance(c, str):
                if c.strip() and n.tag not in ("ul", "ol", "table", "tr"):
                    self.emit(kind="para", html=escape(c.strip(), quote=False), quote=quote)
                continue
            if self.skipped(c):
                continue
            self.mark(c)
            tag = c.tag
            if tag in self.cfg["heading_map"]:
                html = self.inline(c).strip()
                if html:
                    self.emit(kind="heading", level=self.cfg["heading_map"][tag], html=html)
            elif self.is_box(c):
                head = next((h for h in c.children if isinstance(h, Node) and h.tag in self.cfg["heading_map"]), None)
                label = next((x for x in sorted(c.classes()) if x in self.cfg["box_classes"]), None) or "Box"
                self.emit(kind="box_start", label=label.title(), title=self.inline(head).strip() if head else "")
                if head is not None:
                    c.children.remove(head)
                self.walk(c, quote)
                self.emit(kind="box_end")
            elif tag == "p" or (tag == "div" and not any(isinstance(x, Node) and x.tag in BLOCK_TAGS for x in c.children)):
                html = self.inline(c).strip()
                if html:
                    self.emit(kind="para", html=html, quote=quote)
                for img in self.images_in(c):
                    self.emit(kind="photo", src=self.img_path(img), caption=img.attrs.get("alt", ""))
            elif tag == "blockquote":
                self.walk(c, quote=True)
            elif tag in ("ul", "ol"):
                items = [self.inline(li).strip() for li in c.children if isinstance(li, Node) and li.tag == "li"]
                items = [x for x in items if x]
                if items:
                    self.emit(kind="bullets", items=items, numbered=tag == "ol")
            elif tag == "figure":
                cap = c.find("figcaption")
                caption = self.inline(cap).strip() if cap is not None else ""
                imgs = list(self.images_in(c))
                table = c.find("table")
                if table is not None:
                    self.emit(kind="table", rows=self.rows(table), caption=caption)
                elif imgs:
                    self.emit(kind="figure", src=self.img_path(imgs[0]), caption=caption)
            elif tag == "img":
                self.emit(kind="photo", src=self.img_path(c), caption=c.attrs.get("alt", ""))
            elif tag == "table":
                cap = c.find("caption")
                self.emit(kind="table", rows=self.rows(c), caption=self.inline(cap).strip() if cap is not None else "")
            elif tag == "pre":
                text = c.text().strip()
                if text:
                    self.emit(kind="para", html=escape(text, quote=False), quote=True, whole=True)
            elif tag in ("hr", "br"):
                continue
            else:
                self.walk(c, quote)

    def rows(self, table):
        out = []
        for tr in Package._all(table, "tr"):
            cells = [self.inline(td).strip() for td in tr.children if isinstance(td, Node) and td.tag in ("td", "th")]
            if any(cells):
                out.append(" | ".join(cells))
        return out


# ---------------------------------------------------------------- chapters


class EpubChapter(ChapterBase):
    def __init__(self, book, pkg, num, title, events):
        super().__init__(book, num, title)
        self.pkg, self.events = pkg, events
        self.cfg = book.cfg["epub"]
        self.fig_label = re.compile(self.cfg["figure_label_pattern"], re.I)
        self.heading_num = re.compile(self.cfg["heading_number"])

    def para(self, html, kind="para", whole=False):
        html = clean_markup(html)
        parts = [html] if whole else split_sentences(html)
        return {"type": kind, "id": "", "page": None, "sentences": [{"id": "", "html": s} for s in parts],
                "terms": [t.strip(" ,.;:") for t in re.findall(r"<b>([^<]+)</b>", html)]}

    def save_image(self, path, stem):
        ext = PurePosixPath(path).suffix.lower() or ".png"
        fname = f"{self.cid}-{stem}{ext}"
        self.book.img_dir.mkdir(parents=True, exist_ok=True)
        try:
            (self.book.img_dir / fname).write_bytes(self.pkg.zip.read(path))
        except KeyError:
            print(f"  ! image missing from the EPUB: {path}")
        return f"img/{fname}"

    def run(self):
        stack = [self.blocks]
        boxes = []
        last_para = None
        photo_hosts = []
        fig_count = {"figure": 0, "table": 0}
        for ev in self.events:
            k = ev["kind"]
            target = stack[-1]
            if k == "heading":
                if ev["level"] == "title":
                    if not self.title:
                        self.title = re.sub(r"<[^>]+>", "", ev["html"])
                    continue
                text = clean_markup(re.sub(r"</?(?:sub|sup)>", "", ev["html"]))
                m = self.heading_num.match(re.sub(r"<[^>]+>", "", text))
                num = m.group(1).rstrip(".") if m else ""
                hid = "h" + num.replace(".", "-") if num else "hA" + self.new_id("hA")[2:].lstrip("0")
                target.append({"type": "heading", "level": ev["level"], "num": num, "id": hid,
                               "text": re.sub(r"<[^>]+>", "", m.group(2) if m else text)})
            elif k == "para":
                last_para = self.para(ev["html"], "quote" if ev.get("quote") else "para", ev.get("whole", False))
                target.append(last_para)
            elif k == "bullets":
                last_para = {"type": "bullets", "id": "", "page": None,
                             "sentences": [{"id": "", "html": clean_markup(x)} for x in ev["items"]],
                             "terms": [t.strip(" ,.;:") for x in ev["items"] for t in re.findall(r"<b>([^<]+)</b>", x)]}
                target.append(last_para)
            elif k == "box_start":
                box = {"type": "box", "id": self.new_id("box"), "label": ev["label"], "title": ev["title"],
                       "blocks": [], "page": None}
                target.append(box)
                boxes.append(box)
                stack.append(box["blocks"])
            elif k == "box_end":
                if len(stack) > 1:
                    stack.pop()
            elif k in ("figure", "table"):
                cap = re.sub(r"<[^>]+>", "", ev["caption"])
                m = self.fig_label.match(cap)
                if k == "figure" and not m:
                    # an unlabelled figure is a picture with a caption
                    self.add_photo(ev["src"], cap, last_para, photo_hosts)
                    continue
                kind = "table" if (k == "table" or (m and m.group(1).lower().startswith("table"))) else "figure"
                fig_count[kind] += 1
                num = m.group(2).rstrip(".") if m else str(fig_count[kind])
                bid = f"{kind[:3]}{num.replace('.', '-')}"  # ids and file names without dots
                blk = {"type": kind, "id": bid, "label": f"{kind.title()} {num}", "page": None,
                       "title": cap[m.end():].strip() if m else cap, "caption": "",
                       "src": self.save_image(ev["src"], bid) if k == "figure" else ""}
                if k == "table":
                    blk["rows"] = ev["rows"]
                target.append(blk)
            elif k == "photo":
                self.add_photo(ev["src"], re.sub(r"<[^>]+>", "", ev["caption"]), last_para, photo_hosts)
        # drop boxes that ended up empty (decorative asides)
        for box in boxes:
            if not box["blocks"]:
                for lst in self.block_lists(self.blocks, [self.blocks]):
                    if box in lst:
                        lst.remove(box)
        self.apply_patches()
        self.renumber()
        self.place_figures()
        for photo, host in photo_hosts:
            photo["para"] = host["id"] if host else None

    def add_photo(self, src, caption, host, hosts):
        pid = self.new_id("img")
        photo = {"id": pid, "src": self.save_image(src, pid), "caption": caption, "para": None}
        self.photos.append(photo)
        hosts.append((photo, host))


def chapter_entries(pkg, cfg):
    """[(number, title, toc index)] for the TOC entries that are chapters."""
    pat = re.compile(cfg["chapters"].get("toc_pattern") or cfg["chapters"]["outline_pattern"])
    out, seen = [], set()
    for i, (label, path, frag, depth) in enumerate(pkg.toc):
        m = pat.match(label)
        if m:
            num = int(m.group(1))
            if num not in seen:
                seen.add(num)
                out.append((num, m.group(2).strip() if m.lastindex and m.lastindex >= 2 else label, i))
    return out


def extract(book, nums):
    cfg = book.cfg
    cfg["epub"] = {**EPUB_DEFAULTS, **(cfg.get("epub") or {})}
    pkg = Package(book.source)
    if not pkg.toc:
        raise SystemExit("the EPUB has no table of contents (nav or NCX)")
    lin = Linearizer(pkg, cfg["epub"])
    entries = chapter_entries(pkg, cfg)
    if not entries:
        labels = "\n  ".join(e[0] for e in pkg.toc[:30])
        raise SystemExit(f"no TOC entry matched chapters.toc_pattern; the TOC starts with:\n  {labels}")

    def pos(i):
        _, path, frag, _ = pkg.toc[i]
        return lin.anchor_pos.get((path, frag), lin.anchor_pos.get((path, "")))

    for num, title, i in entries:
        if nums and num not in nums:
            continue
        start, depth = pos(i), pkg.toc[i][3]
        # the chapter runs to the next TOC entry at the same or a higher level
        later = [pos(j) for j in range(i + 1, len(pkg.toc)) if pkg.toc[j][3] <= depth]
        later = [p for p in later if p is not None and p > start]
        end = min(later) if later else len(lin.events)
        ch = EpubChapter(book, pkg, num, title, lin.events[start:end])
        ch.run()
        ch.write()
