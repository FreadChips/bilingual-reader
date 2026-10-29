"""HTML source, such as an SEC filing (10-K, 10-Q, 20-F) saved as one HTML file.

Chapters are the filing's Items, found by their bold heading lines ("Item 7.  Management's
Discussion and Analysis ..."). An Item with a letter (7A) belongs to the chapter of its number and
opens a level-1 heading there. Text before the first Item (cover page, table of contents) and Part
headings are left out, and so is page furniture (running footers such as "Apple Inc. | 2025 Form
10-K | 20").

Every block of text is one element without block children: a bold one is a heading, one opening
with a bullet is a list item, the rest are paragraphs. Tables become grids straight from their
cells (tablekit's format): empty spacer columns are dropped, and the cells that hold only "$", ")"
or "%" are joined to their numbers. A table with no number in it is page layout, read as text.
"""
import re
from html import escape

from chapterkit import ChapterBase, extract_each
from extract_epub import BLOCK_TAGS, Node, parse_html
from tablekit import is_numeric
from textkit import clean_markup, split_sentences

HTML_DEFAULTS = {
    "item_pattern": r"^Item\s+(\d+)([A-Z]?)\.\s*(.*)$",   # group 1 number, 2 letter, 3 title
    "furniture": [r"\|\s*\d{4}\s+Form\s+10-[KQ]\s*\|\s*\d+\s*$", r"^\s*\d{1,3}\s*$", r"^PART\s+[IV]+$",
                  r"^Table of Contents$"],
    "heading_max": 160,                                     # longest bold line taken as a heading
}
BULLET = "•●▪■◦‣·"
TAKEN = "<$>"  # a "$" cell whose sign moved into the number after it


def style(node):
    return {k.strip().lower(): v.strip() for k, v in
            (x.split(":", 1) for x in (node.attrs.get("style") or "").split(";") if ":" in x)}


def is_bold(node):
    s = style(node)
    return node.tag in ("b", "strong") or s.get("font-weight") in ("700", "bold", "800", "900")


def is_italic(node):
    return node.tag in ("i", "em") or style(node).get("font-style") == "italic"


def has_block(node):
    return any(isinstance(c, Node) and (c.tag in BLOCK_TAGS or has_block(c)) for c in node.children)


def inline(node, bold=False, italic=False):
    """(html with <b>/<i>, plain text, share of letters set in bold) of an element without blocks."""
    parts = []

    def walk(n, b, i):
        for c in n.children:
            if isinstance(c, str):
                parts.append((c.replace("\xa0", " "), b, i))
            elif c.tag == "br" or (isinstance(c, Node) and c.tag in BLOCK_TAGS and parts):
                parts.append((" ", b, i))
                if c.tag != "br":
                    walk(c, b or is_bold(c), i or is_italic(c))
            elif c.tag in ("sup", "sub"):
                t = c.text().replace("\xa0", " ").strip()
                if t:
                    parts.append((f"<{c.tag}>{escape(t, quote=False)}</{c.tag}>", b, i))
            elif isinstance(c, Node) and c.tag not in ("script", "style", "img"):
                walk(c, b or is_bold(c), i or is_italic(c))
    walk(node, bold or is_bold(node), italic or is_italic(node))
    html, plain, bold_chars, letters = [], [], 0, 0
    for t, b, i in parts:
        raw = t if t.startswith(("<sup>", "<sub>")) else escape(t, quote=False)
        if not t.startswith(("<sup>", "<sub>")):
            plain.append(t)
            n = sum(ch.isalnum() for ch in t)
            letters += n
            bold_chars += n if b else 0
        if t.strip() and (b or i) and not t.startswith(("<sup>", "<sub>")):
            raw = (f"<b>{raw}</b>" if b else raw)
            raw = (f"<i>{raw}</i>" if i else raw)
        html.append(raw)
    text = re.sub(r"\s+", " ", "".join(plain)).strip()
    return clean_markup(re.sub(r"\s+", " ", "".join(html))), text, (bold_chars / letters if letters else 0)


class Reader:
    """The document as a flat list of events: ("text", html, text, bold share, italic) and ("table", node)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.furniture = [re.compile(p) for p in cfg["furniture"]]
        self.events = []

    def walk(self, node):
        for c in node.children:
            if not isinstance(c, Node):
                if c.strip():
                    self.text_event(Node("span", {}), c)
                continue
            if c.tag in ("script", "style", "head", "title", "hr"):
                continue
            if c.tag == "table":
                self.events.append(("table", c))
            elif has_block(c):
                self.walk(c)
            elif c.tag in BLOCK_TAGS or c.tag.startswith("efx") or c.tag in ("span", "font", "a"):
                self.text_event(c)

    def text_event(self, node, raw=None):
        if raw is not None:
            node = Node("span", {})
            node.children.append(raw)
        html, text, bold = inline(node)
        html, text = (re.sub(r"^\((\d{1,2})\)(?=[A-Za-z<])", r"(\1) ", x) for x in (html, text))
        if not text or any(p.search(text) for p in self.furniture):
            return
        italic = bool(re.fullmatch(r"(<i>[^<]*</i>\s*)+", html))
        self.events.append(("text", html, text, bold, italic))


def table_grid(node):
    """{"header": n, "rows": [...]} in tablekit's format, or None for a table without numbers."""
    rows = []
    taken = {}  # column -> rows still covered by a cell with rowspan from a row above
    for tr in _all(node, "tr"):
        cells, col = [], 0
        for td in tr.children:
            if isinstance(td, Node) and td.tag in ("td", "th"):
                html, text, bold = inline(td)
                try:
                    span = max(1, int(td.attrs.get("colspan") or 1))
                    down = max(1, int(td.attrs.get("rowspan") or 1))
                except ValueError:
                    span, down = 1, 1
                while taken.get(col, 0) > 0:  # a cell from a row above reaches down into this one
                    col += 1
                cells.append([text, span, bold > 0.5, col])
                if down > 1:
                    for k in range(col, col + span):
                        taken[k] = down  # counted down below, after the row
                col += span
        taken = {k: v - 1 for k, v in taken.items() if v - 1 > 0}
        if cells:
            rows.append(cells)
    if not rows:
        return None
    # cells that hold only "$" join the number after them; ")", "%" and ")%" join the one before
    for row in rows:
        for k, c in enumerate(row):
            if c[0] in ("$", "€", "£", "¥"):
                nxt = next((d for d in row[k + 1:] if d[0]), None)
                if nxt is not None and is_numeric(nxt[0]):
                    nxt[0], c[0] = c[0] + nxt[0], TAKEN  # TAKEN: the cell now belongs to the number after it
            elif c[0] in (")", "%", ")%", "%)"):
                prv = next((d for d in reversed(row[:k]) if d[0]), None)
                if prv is not None:
                    prv[0], c[0] = prv[0] + c[0], ""
    # cells as (first column, columns covered, text, bold)
    laid = [[[c[3], c[1], c[0], c[2]] for c in row] for row in rows]
    # the emptied "$" cell and its number are one cell; other empty cells are spacers
    for cells in laid:
        k = 0
        while k + 1 < len(cells):
            a, b = cells[k], cells[k + 1]
            if a[2] == TAKEN and b[2] and a[0] + a[1] == b[0]:
                b[0], b[1] = a[0], a[1] + b[1]
                del cells[k]
            else:
                k += 1
        for c in cells:
            c[2] = "" if c[2] == TAKEN else c[2]
    # columns: the positions where a filled cell starts in some row
    starts = sorted({c[0] for cells in laid for c in cells if c[2]})
    if len(starts) < 2:
        return None
    out = []
    for cells in laid:
        line, used = [], 0
        for k, s in enumerate(starts):
            if k < used:
                continue
            cell = next((c for c in cells if c[2] and c[0] == s), None)
            if cell is None:
                cell = next((c for c in cells if c[0] <= s < c[0] + c[1] and not c[2]), None)
                line.append({"t": "", "span": 1, "bold": False})
                used = k + 1
                continue
            end = cell[0] + cell[1]
            n = sum(1 for x in starts[k:] if x < end) or 1
            line.append({"t": cell[2], "span": n, "bold": cell[3]})
            used = k + n
        if any(c["t"] for c in line):
            out.append(line)
    if not any(is_numeric(c["t"]) for row in out for c in row if c["t"]):
        return None
    # header: the leading rows with no number in them (a year such as 2025 is a label there)
    figure = lambda s: is_numeric(s) and not re.fullmatch(r"\s*(19|20)\d\d\s*", s)  # noqa: E731
    header = 0
    while header < min(4, len(out) - 1) and not any(figure(c["t"]) for c in out[header] if c["t"]):
        header += 1
    return {"header": header,
            "rows": [[c["t"] if c["span"] == 1 else {"t": c["t"], "span": c["span"]} for c in row] for row in out]}


def table_text(node):
    """A layout table (no numbers): its rows as plain lines."""
    lines = []
    for tr in _all(node, "tr"):
        cells = [inline(td)[0] for td in tr.children if isinstance(td, Node) and td.tag in ("td", "th")]
        line = " ".join(c for c in cells if c.strip())
        if line.strip():
            lines.append(line)
    return lines


def _all(node, tag):
    out = []
    for c in node.children:
        if isinstance(c, Node):
            if c.tag == tag:
                out.append(c)
            else:
                out.extend(_all(c, tag))
    return out


class HtmlChapter(ChapterBase):
    def __init__(self, book, num, title, events, cfg):
        super().__init__(book, num, title)
        self.events, self.cfg = events, cfg
        self.item = re.compile(cfg["item_pattern"])

    def para(self, html, kind="para"):
        html = clean_markup(html)
        return {"type": kind, "id": "", "page": None,
                "sentences": [{"id": "", "html": s} for s in split_sentences(html)],
                "terms": []}

    def heading(self, text, level):
        m = re.match(r"^(Note\s+\d+|Item\s+\d+[A-Z]?)[.:–—\-\s]+(.*)$", text)
        num = re.sub(r"\s+", " ", m.group(1)) if m else ""
        hid = "h" + re.sub(r"\W+", "", num) if num else "hA" + self.new_id("hA")[2:].lstrip("0")
        return {"type": "heading", "level": level, "num": num, "id": hid, "text": m.group(2) if m else text}

    def run(self):
        bullets = None
        ntab = 0
        for ev in self.events:
            if ev[0] == "table":
                bullets = None
                grid = table_grid(ev[1])
                if grid is None:
                    for line in table_text(ev[1]):
                        self.blocks.append(self.para(line))
                    continue
                ntab += 1
                self.blocks.append({"type": "table", "id": f"tab{ntab}", "label": f"Table {ntab}", "page": None,
                                    "title": "", "caption": "", "src": "", "grid": grid})
                continue
            _, html, text, bold, italic = ev
            m = self.item.match(text)
            if m and bold > 0.8:
                bullets = None
                if m.group(2):  # Item 7A inside chapter 7
                    self.blocks.append(self.heading(text, 1))
                continue
            if text[:1] in BULLET:
                item = clean_markup(re.sub(r"^\s*(<[bi]>)?\s*[" + BULLET + r"]\s*", r"\1", html))
                if bullets is None:
                    bullets = {"type": "bullets", "id": "", "page": None, "sentences": [], "terms": []}
                    self.blocks.append(bullets)
                bullets["sentences"].append({"id": "", "html": item})
                continue
            bullets = None
            short = len(text) <= self.cfg["heading_max"] and not re.search(r"[.;,]$", text)
            if bold > 0.8 and short:
                level = 1 if re.match(r"^(Note\s+\d+|[A-Z][A-Z ,'’&\-]+$)", text) else 2
                self.blocks.append(self.heading(text, level))
            elif italic and short and len(text) <= 80:
                self.blocks.append(self.heading(text, 2))
            else:
                self.blocks.append(self.para(html))
        self.apply_patches()
        self.renumber()


def items(book):
    """The chapter numbers (Items) the document has."""
    cfg = {**HTML_DEFAULTS, **(book.cfg.get("html") or {})}
    return set(read_chapters(book, cfg))


def read_chapters(book, cfg):
    data = book.source.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    reader = Reader(cfg)
    reader.walk(parse_html(text))
    item = re.compile(cfg["item_pattern"])
    chapters, cur = {}, None
    for ev in reader.events:
        if ev[0] == "text":
            m = item.match(ev[2])
            if m and ev[3] > 0.8:
                n = int(m.group(1))
                if not m.group(2) or n not in chapters:
                    if n not in chapters:
                        chapters[n] = {"title": m.group(3).strip(), "events": []}
                    cur = n
        if cur is not None:
            chapters[cur]["events"].append(ev)
    return chapters


def extract(book, nums):
    cfg = {**HTML_DEFAULTS, **(book.cfg.get("html") or {})}
    chapters = read_chapters(book, cfg)
    if not chapters:
        raise SystemExit("no Item headings found: check html.item_pattern")

    def extract_one(num):
        if num not in chapters:
            raise SystemExit(f"chapter {num} not in the document (found Items {sorted(chapters)})")
        ch = HtmlChapter(book, num, chapters[num]["title"], chapters[num]["events"], cfg)
        ch.run()
        ch.write()
    extract_each(nums or sorted(chapters), extract_one)
