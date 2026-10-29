"""Export the bilingual reader (reader/data/*.js) as one EPUB 3 book for phone reading apps.

Each chapter becomes one XHTML file: original sentence, translation, notes, figures, boxes,
Quick Quizzes (with hints), the end-of-chapter quiz (answers collected after the questions),
summary and key terms. A book-wide glossary index closes the book. No scripts: the reading
app supplies position memory, search and night mode.

Usage:  python export_epub.py [--book DIR]  ->  export/<book.epub_name>.epub
Book title, author, subject and the source note come from book.yaml.
"""
import datetime
import json
import re
import sys
import uuid
import zipfile
from html import escape
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I, which leaves out the script's folder
from bookkit import open_book  # noqa: E402

BOOK = None  # set in main()
ROOT = DATA = OUT = BOOK_ID = None
NOTE = "解读"  # label of the subject notes, e.g. 经济学解读
SUBJECT = ""
PAPER = False  # book.kind is "paper" or "filing": no chapter numbers
KIND = "book"


def chap_name(ch):
    return ch["title_zh"] if PAPER else f'第{ch["number"]}章　{ch["title_zh"]}'


def load_js(path, var):
    text = path.read_text(encoding="utf-8")
    start = text.index("{", text.index(var))
    return json.loads(text[start:].rstrip().rstrip(";"))


# ---------------------------------------------------------------- text helpers
def esc(s):
    return escape(str(s or ""), quote=True)


def md(s):
    """Plain annotation text with **bold** (same rule as the web reader)."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc(s))


def xhtml(s):
    """Source HTML from the PDF (italics, subscripts, fractions) made well-formed XML."""
    s = re.sub(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)", "&amp;", s or "")
    s = re.sub(r"<(?!/?(?:i|b|sub|sup|span|div|br)\b)", "&lt;", s)  # a bare "MR < P" in the text
    return s.replace("<br>", "<br/>")


def title_case(s):
    if s != s.upper():
        return s
    small = {"a", "an", "the", "of", "and", "or", "in", "on", "to", "for", "by", "with", "at"}
    return " ".join(w if i and w in small else w.capitalize() for i, w in enumerate(s.lower().split()))


def tag(label, kind):
    return f'<span class="tag {kind}">{label}</span>'


# ---------------------------------------------------------------- images
class Images:
    """Copies figure images into the book, as JPEG when that is smaller than the PNG."""

    def __init__(self):
        self.files = {}  # reader src -> (book path, bytes, media type)

    TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".svg": "image/svg+xml", ".webp": "image/webp"}

    def __call__(self, src):
        if src not in self.files:
            path = ROOT / "reader" / src
            raw = path.read_bytes()
            stem, ext = Path(src).stem, Path(src).suffix.lower()
            self.files[src] = (f"img/{stem}{ext}", raw, self.TYPES.get(ext, "image/png"))
            if ext == ".png":  # page clips from a PDF: keep PNG unless JPEG is clearly smaller
                pix = pymupdf.Pixmap(str(path))
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                jpg = pix.tobytes("jpeg", jpg_quality=82)
                if len(jpg) < len(raw) * 0.8:
                    self.files[src] = (f"img/{stem}.jpg", jpg, "image/jpeg")
        return self.files[src][0]


# ---------------------------------------------------------------- chapter rendering
class Chapter:
    def __init__(self, ch, img):
        self.ch, self.img = ch, img
        self.file = f"{ch['id']}.xhtml"

    def sentence(self, s, bullet=False):
        h = f'<div class="sent"><p class="en">{"• " if bullet else ""}{xhtml(s["en"])}</p>'
        if s.get("zh"):
            h += f'<p class="zh">{md(s["zh"])}</p>'
        if s.get("n"):
            h += f'<p class="note econ">{tag(NOTE, "econ")}{md(s["n"])}</p>'
        if s.get("l"):
            h += f'<p class="note lang">{tag("语言", "lang")}{md(s["l"])}</p>'
        return h + "</div>"

    def para(self, p):
        num = int(p["id"][1:]) if p["id"][1:].isdigit() else p["id"]
        h = f'<div class="para {p["type"]}" id="{p["id"]}"><p class="pmeta">¶ {num}{"　原书 p." + str(p["page"]) if p.get("page") is not None else ""}</p>'
        h += "".join(self.sentence(s, p["type"] == "bullets") for s in p["sentences"])
        if p.get("sum"):
            h += f'<p class="sum">{tag("要点", "sum")}{md(p["sum"])}</p>'
        h += "</div>"
        for t in p.get("terms") or []:
            h += (f'<div class="term"><p class="t-label">KEY TERM · 术语</p>'
                  f'<p class="t-head"><b>{esc(t["term"])}</b>　{esc(t["zh"])}</p>'
                  f'<p class="t-def">{esc(t["def"])}</p><p class="t-def zh">{esc(t["def_zh"])}</p></div>')
        for ph in p.get("photos") or []:
            h += (f'<div class="photo"><img src="{self.img(ph["src"])}" alt="{esc(ph["caption"])}"/>'
                  f'<p class="cap-en">{esc(ph["caption"])}</p><p class="cap-zh">{md(ph.get("zh"))}</p>'
                  + (f'<p class="cap-n">{tag(NOTE, "econ")}{md(ph["n"])}</p>' if ph.get("n") else "") + "</div>")
        return h

    def principles(self):
        rows, group = "", None
        for x in self.ch["blocks"]:
            if x["type"] != "heading" or "Conclusion" in x["text"]:
                continue
            if x["level"] == 1:
                rows += f'<tr><th colspan="2">{esc(x["text"])}<br/>{esc(x["zh"])}</th></tr>'
                group = x
            elif group:
                m = re.match(r"^Principle (\d+):\s*(.*)$", x["text"])
                zh = re.sub(r"^原理[一二三四五六七八九十]+：", "", x["zh"])
                rows += (f'<tr><td class="k">{m.group(1) if m else ""}</td><td>{esc(m.group(2) if m else x["text"])}'
                         f'<br/><span class="zh">{esc(zh)}</span></td></tr>')
        return f'<table class="principles">{rows}</table>'

    def figure(self, b):
        kind = "表" if b["type"] == "table" else "图"
        label = b["label"].upper()
        h = (f'<div class="fig" id="{b["id"]}"><p class="fig-label">{esc(label)}　{kind} {esc(b["label"].split(" ")[1])}</p>'
             f'<p class="fig-title">{esc(b["title"])}<br/><span class="zh">{esc(b["title_zh"])}</span></p>')
        if b["type"] == "table" and re.search("Ten Principles", b["title"], re.I):
            h += self.principles()
        elif b.get("rows"):
            h += "<ol class=\"rows\">" + "".join(f'<li>{esc(r["en"])}<br/><span class="zh">{md(r["zh"])}</span></li>'
                                                for r in b["rows"]) + "</ol>"
        elif b.get("grid"):
            g = b["grid"]
            h += '<div class="tbl"><table class="grid">'
            for i, row in enumerate(g["rows"]):
                tag_ = "th" if i < g["header"] else "td"
                h += "<tr>"
                for c in row:
                    span = ' colspan="%d"' % c["span"] if c["span"] > 1 else ""
                    if c["en"].strip() and re.fullmatch(r"[\s\d.,:%±+\-−–—×()\[\]†‡*/<>=~$€£¥]*", c["en"]):
                        span += ' class="num"'
                    zh = f'<br/><span class="zh">{md(c["zh"])}</span>' if c["zh"] and c["zh"] != c["en"] else ""
                    h += f"<{tag_}{span}>{esc(c['en'])}{zh}</{tag_}>"
                h += "</tr>"
            h += "</table></div>"
            if b.get("src"):
                h += f'<div class="fig-img"><img src="{self.img(b["src"])}" alt="{esc(b["label"] + " " + b["title"])}"/></div>'
        elif b.get("src"):
            h += f'<div class="fig-img"><img src="{self.img(b["src"])}" alt="{esc(b["label"] + " " + b["title"])}"/></div>'
        if b.get("caption"):
            h += f'<p class="cap-en">{esc(b["caption"])}</p><p class="cap-zh">{md(b["caption_zh"])}</p>'
        if b.get("n"):
            h += f'<p class="note econ">{tag(NOTE, "econ")}{md(b["n"])}</p>'
        if b.get("source"):
            h += f'<p class="src">{esc(b["source"])}</p>'
        return h + "</div>"

    def box(self, b):
        h = (f'<div class="box" id="{b["id"]}"><p class="box-label">{esc(b["label"])} · {esc(b["label_zh"])}</p>'
             f'<p class="box-title">{esc(title_case(b["title"]))}<br/><span class="zh">{esc(b["title_zh"])}</span></p>')
        if b.get("intro"):
            h += f'<p class="news-intro">{esc(b["intro"])}<br/><span class="zh">{md(b.get("intro_zh"))}</span></p>'
        if b.get("headline"):
            h += f'<p class="news-head">{esc(b["headline"])}<br/><span class="zh">{md(b.get("headline_zh"))}</span></p>'
            if b.get("byline"):
                h += f'<p class="byline">{esc(b["byline"])}{"　" + esc(b["byline_zh"]) if b.get("byline_zh") else ""}</p>'
        h += "".join(self.block(x) for x in b["blocks"])
        if b.get("author"):
            h += f'<p class="box-src"><i>{esc(b["author"])}</i>{"　" + esc(b["author_zh"]) if b.get("author_zh") else ""}</p>'
        if b.get("source"):
            h += f'<p class="box-src">{esc(b["source"])}{"　" + esc(b["source_zh"]) if b.get("source_zh") else ""}</p>'
        return h + "</div>"

    def experts(self, b):
        h = (f'<div class="box experts" id="{b["id"]}"><p class="box-label">ASK THE EXPERTS · 问问专家</p>'
             f'<p class="box-title">{esc(b["title"])}<br/><span class="zh">{esc(b["title_zh"])}</span></p>')
        for it in b.get("items") or []:
            r = it.get("results") or {}
            h += (f'<p class="en">{esc(it["proposition"])}</p><p class="zh">{md(it["zh"])}</p>'
                  f'<p class="poll">同意 {r.get("agree", 0)}%　·　不确定 {r.get("uncertain", 0)}%　·　不同意 {r.get("disagree", 0)}%</p>')
        h += f'<p class="src">{esc(b.get("source"))}</p>'
        if b.get("n"):
            h += f'<p class="note econ">{tag(NOTE, "econ")}{md(b["n"])}</p>'
        return h + "</div>"

    def quickquiz(self, b):
        items = "".join(
            f'<li><p class="en">{esc(it["en"])}</p><p class="zh">{md(it["zh"])}</p>'
            + (f'<p class="hint">{tag("参考思路", "hint")}{md(it["hint"])}</p>' if it.get("hint") else "") + "</li>"
            for it in b["items"])
        return f'<div class="quiz" id="{b["id"]}"><p class="quiz-title">Quick Quiz · 随堂自测</p><ol>{items}</ol></div>'

    def heading(self, b):
        if b["level"] == 1:
            return (f'<h2 id="{b["id"]}"><span class="num">{esc(b["num"])}</span> {esc(b["text"])}'
                    f'<br/><span class="zh">{esc(b["zh"])}</span></h2>')
        m = re.match(r"^Principle (\d+):\s*(.*)$", b["text"])
        badge = f"原理 {m.group(1)}" if m else esc(b["num"])
        zh = re.sub(r"^原理[一二三四五六七八九十]+：", "", b["zh"])
        return (f'<h3 id="{b["id"]}">{f"<span class=\"num\">{badge}</span> " if badge else ""}{esc(m.group(2) if m else b["text"])}'
                f'<br/><span class="zh">{esc(zh)}</span></h3>')

    def block(self, b):
        t = b["type"]
        if t == "heading":
            return self.heading(b)
        if t in ("para", "quote", "bullets"):
            return self.para(b)
        if t == "box":
            return self.box(b)
        if t == "experts":
            return self.experts(b)
        if t == "math":
            return (f'<div class="math" id="{b["id"]}"><p>{xhtml(b["html"])}</p>'
                    + (f'<p class="zh">{xhtml(b["zh"])}</p>' if b.get("zh") else "") + "</div>")
        if t == "quickquiz":
            return self.quickquiz(b)
        if t in ("figure", "table"):
            return self.figure(b)
        raise ValueError(f"unknown block type {t}")

    def end(self):
        ch, e, h = self.ch, self.ch.get("end") or {}, ""
        if e.get("quiz"):
            h += '<h2 id="end-quiz">Chapter Quick Quiz<br/><span class="zh">章末测验</span></h2>'
            for i, q in enumerate(e["quiz"], 1):
                opts = "".join(f'<li><b>{o["key"]}.</b> {esc(o["en"])}<br/><span class="zh">{esc(o["zh"])}</span></li>'
                               for o in q["options"])
                h += (f'<div class="mcq" id="q-{q["id"]}"><p class="en">{i}. {esc(q["stem"])} …</p>'
                      f'<p class="zh">{esc(q["stem_zh"])}……</p><ul class="opts">{opts}</ul>'
                      f'<p class="to-ans"><a href="#a-{q["id"]}">查看答案 →</a></p></div>')
            h += '<h3 id="end-answers">答案与解析</h3><ol class="answers">'
            h += "".join(f'<li id="a-{q["id"]}"><b>{q["answer"]}</b>　{md(q["explain"])}　<a href="#q-{q["id"]}">↩</a></li>'
                         for q in e["quiz"])
            h += "</ol>"
        if e.get("summary"):
            h += '<h2 id="end-summary">Summary<br/><span class="zh">本章小结</span></h2><ol class="summary">'
            h += "".join(f'<li><p class="en">{esc(s["en"])}</p><p class="zh">{md(s["zh"])}</p></li>' for s in e["summary"])
            h += "</ol>"
        if ch.get("glossary"):
            h += '<h2 id="end-gloss">Key Concepts<br/><span class="zh">本章术语</span></h2><dl class="gloss">'
            for g in ch["glossary"]:
                link = f'　<a href="#{g["para"]}">p.{g["page"]}</a>' if g.get("para") else ""
                h += (f'<dt><b>{esc(g["term"])}</b>　{esc(g["zh"])}{link}</dt>'
                      f'<dd>{esc(g["def"])}</dd><dd class="zh">{esc(g["def_zh"])}</dd>')
            h += "</dl>"
        return h

    def render(self):
        ch = self.ch
        body = (f'<section epub:type="chapter" class="chapter"><header class="chap-head">'
                + ("" if PAPER else f'<p class="eyebrow">Chapter {ch["number"]}</p>')
                + f'<h1>{esc(ch["title"])}<br/><span class="zh">{esc(chap_name(ch))}</span></h1>'
                + (f'<p class="stats">原书 p.{ch["pages"][0]}–{ch["pages"][1]}</p>' if ch["pages"][0] is not None else "")
                + '</header>')
        if ch.get("guide"):
            body += f'<div class="guide"><p class="guide-title">{"导读" if PAPER else "本章导读"}</p>' + "".join(f"<p>{md(p)}</p>" for p in ch["guide"]) + "</div>"
        body += "".join(self.block(b) for b in ch["blocks"])
        body += self.end()
        if ch.get("appendix"):
            body += '<h2 id="appendix">Appendix<br/><span class="zh">附录</span></h2>'
            body += "".join(f'<div class="guide"><p>{md(p)}</p></div>' for p in ch.get("appendix_guide") or [])
            body += "".join(self.block(b) for b in ch["appendix"])
        body += "</section>"
        return page(chap_name(ch).replace("　", " "), body)

    def toc(self):
        """(title, href, children) for the navigation document."""
        kids, cur = [], None
        for b in self.ch["blocks"]:
            if b["type"] != "heading":
                continue
            if b["level"] == 1:
                cur = (f'{b["num"]} {b["zh"]}', f'{self.file}#{b["id"]}', [])
                kids.append(cur)
            elif cur:
                m = re.match(r"^Principle (\d+):", b["text"])
                cur[2].append((b["zh"] if m else f'{b["num"]} {b["zh"]}'.strip(), f'{self.file}#{b["id"]}', []))
        e = self.ch.get("end") or {}
        if e.get("quiz"):
            kids.append(("章末测验", f"{self.file}#end-quiz", []))
        if e.get("summary"):
            kids.append(("本章小结", f"{self.file}#end-summary", []))
        if self.ch.get("glossary"):
            kids.append(("本章术语", f"{self.file}#end-gloss", []))
        if self.ch.get("appendix"):
            kids.append(("附录", f"{self.file}#appendix", []))
        return (chap_name(self.ch), self.file, kids)


# ---------------------------------------------------------------- book pages
def page(title, body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN" xml:lang="zh-CN">'
            f'<head><meta charset="utf-8"/><title>{esc(title)}</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>'
            f"<body>{body}</body></html>")


def title_page(book, chapters):
    n_sent = sum(len(p["sentences"]) for c in chapters for p in iter_paras(c.ch))
    body = f"""<section epub:type="titlepage" class="titlepage">
<p class="eyebrow">{esc(BOOK.cfg["book"]["author"])}</p>
<h1>{esc(book["title"])}<br/><span class="zh">{esc(book["title_zh"])}</span></h1>
<p class="subtitle">双语精读本 · 原文 / 译文 / {esc(SUBJECT)}{NOTE}</p>
<p class="stats">{"" if len(chapters) == 1 else f"{len(chapters)} 章 · "}{n_sent:,} 句</p>
<div class="guide"><p class="guide-title">阅读说明</p>
<p>每句英文原文下面紧跟中文译文。重要的地方附有说明：</p>
<p>{tag(NOTE, "econ")}{esc(SUBJECT)}{NOTE}：{"问题与贡献、方法原理、实验读法，以及背景与后续影响。" if PAPER else "概念、逻辑、图表读法，以及与其他章节的联系。"}</p>
<p>{tag("语言", "lang")}语言提示：只在遇到值得注意的词汇或语法时出现。</p>
<p>{tag("要点", "sum")}段落要点：用一句话概括这一段讲了什么。</p>
<p>{"图表放在正文第一次提到它们的地方。" if PAPER else "图表放在正文第一次提到它们的地方。每章末尾依次是章末测验（答案与解析集中在题目后面）、本章小结和本章术语；全书最后有术语索引。"}</p></div>
<p class="legal">{xhtml(BOOK.cfg["book"]["source_note"])}</p>
</section>"""
    return page("双语精读本", body)


def glossary_page(chapters):
    entries = []
    for c in chapters:
        for g in c.ch.get("glossary") or []:
            href = f'{c.file}#{g["para"]}' if g.get("para") else c.file
            entries.append((g["term"].lower(), g, c.ch["number"], href))
    entries.sort(key=lambda e: re.sub(r"[^a-z0-9 ]", "", e[0]))
    body = '<section epub:type="glossary" class="chapter"><h1>Glossary Index<br/><span class="zh">全书术语索引</span></h1><dl class="gloss">'
    letter = ""
    for key, g, num, href in entries:
        first = key[0].upper()
        if first != letter:
            letter = first
            body += f'<dt class="letter">{esc(letter)}</dt>'
        body += (f'<dt><a href="{href}"><b>{esc(g["term"])}</b></a>　{esc(g["zh"])}　<small>第{num}章</small></dt>'
                 f'<dd class="zh">{esc(g["def_zh"])}</dd>')
    return page("全书术语索引", body + "</dl></section>"), len(entries)


def iter_paras(ch):
    def walk(blocks):
        for b in blocks:
            if b["type"] == "box":
                yield from walk(b["blocks"])
            elif b.get("sentences"):
                yield b
    yield from walk(ch["blocks"] + (ch.get("appendix") or []))


def cover_png(book):
    """A plain typographic cover drawn with MuPDF's built-in CJK font."""
    doc = pymupdf.open()
    pg = doc.new_page(width=600, height=900)
    pg.draw_rect(pg.rect, color=None, fill=(0.12, 0.33, 0.31))
    pg.draw_rect(pymupdf.Rect(48, 120, 552, 124), color=None, fill=(0.93, 0.78, 0.45))
    cover = {"title": book["title_zh"], "subtitle": "双语精读本", "english": book["title"],
             **(BOOK.cfg.get("cover") or {})}
    pg.insert_textbox(pymupdf.Rect(48, 150, 552, 260), cover["title"], fontname="china-s", fontsize=50,
                      color=(1, 1, 1), align=0)
    # the CJK font sets Latin letters at full width, so Latin text gets a Latin font of its own
    pg.insert_textbox(pymupdf.Rect(48, 230, 552, 300), cover["subtitle"], fontname="china-s", fontsize=30,
                      color=(1, 1, 1), align=0)
    pg.insert_textbox(pymupdf.Rect(48, 330, 552, 420), cover["english"], fontname="tiro",
                      fontsize=26, color=(0.93, 0.78, 0.45), align=0)
    pg.insert_textbox(pymupdf.Rect(48, 420, 552, 520), f"原文／译文／{SUBJECT}{NOTE}", fontname="china-s", fontsize=24,
                      color=(0.85, 0.92, 0.9), align=0)
    pg.insert_textbox(pymupdf.Rect(48, 780, 552, 820), BOOK.cfg["book"]["author"], fontname="tiro",
                      fontsize=22, color=(0.85, 0.92, 0.9), align=0)
    pg.insert_textbox(pymupdf.Rect(48, 815, 552, 860), "个人学习用", fontname="china-s",
                      fontsize=16, color=(0.85, 0.92, 0.9), align=0)
    return pg.get_pixmap(dpi=144).tobytes("png")


CSS = """
body { margin: 0 0.4em; line-height: 1.6; }
.zh { }
p { margin: 0.25em 0; text-indent: 0; }
h1 { font-size: 1.5em; line-height: 1.3; margin: 0.6em 0 0.3em; }
h2 { font-size: 1.25em; line-height: 1.35; margin: 1.6em 0 0.6em; padding-top: 0.4em; border-top: 2px solid #2a7a72; page-break-after: avoid; }
h3 { font-size: 1.08em; line-height: 1.4; margin: 1.3em 0 0.5em; page-break-after: avoid; }
h1 .zh, h2 .zh, h3 .zh { font-weight: normal; font-size: 0.85em; }
.num { color: #2a7a72; }
.eyebrow { font-size: 0.8em; letter-spacing: 0.12em; text-transform: uppercase; color: #2a7a72; margin-top: 1em; }
.stats, .pmeta, .src, .box-src, .byline { font-size: 0.75em; opacity: 0.7; }
.chap-head { margin-bottom: 1em; }
.guide { margin: 1em 0; padding: 0.6em 0.8em; border-left: 4px solid #2a7a72; background: rgba(42,122,114,0.07); }
.guide-title, .quiz-title, .box-label, .fig-label, .t-label { font-weight: bold; font-size: 0.8em; letter-spacing: 0.06em; color: #2a7a72; }
.para { margin: 1em 0 1.2em; }
.pmeta { margin-bottom: 0.2em; }
.sent { margin: 0 0 0.75em; }
.en { margin: 0; }
p.zh, .sent .zh { margin: 0.1em 0 0; padding-left: 0.6em; border-left: 2px solid rgba(128,128,128,0.35); }
.quote .en { font-style: italic; padding-left: 1em; }
.note { font-size: 0.86em; margin: 0.35em 0 0.2em 0.6em; padding: 0.3em 0.5em; background: rgba(128,128,128,0.08); }
.note.econ { border-left: 3px solid #2a7a72; }
.note.lang { border-left: 3px solid #b07a1f; }
.sum { font-size: 0.88em; margin-top: 0.4em; padding: 0.3em 0.5em; border: 1px dashed rgba(42,122,114,0.6); }
.tag { display: inline-block; font-size: 0.78em; font-weight: bold; color: #fff; background: #2a7a72; padding: 0 0.35em; margin-right: 0.45em; border-radius: 3px; line-height: 1.5; }
.tag.lang { background: #b07a1f; }
.tag.sum { background: #4b6b8a; }
.tag.hint { background: #6b5b95; }
.term { margin: 0.6em 0 1.2em; padding: 0.5em 0.7em; border: 1px solid rgba(42,122,114,0.5); }
.t-def { font-size: 0.9em; }
.photo, .fig { margin: 1.2em 0; page-break-inside: avoid; }
.photo img, .fig-img img { max-width: 100%; height: auto; }
.photo { text-align: center; }
.photo p { text-align: left; }
.fig { padding: 0.5em 0 0.7em; border-top: 1px solid rgba(128,128,128,0.4); border-bottom: 1px solid rgba(128,128,128,0.4); }
.fig-title { font-weight: bold; margin-bottom: 0.4em; }
.fig-title .zh { font-weight: normal; }
.fig-img { text-align: center; margin: 0.4em 0; }
.cap-en, .cap-zh, .cap-n { font-size: 0.86em; }
.cap-n { margin-top: 0.3em; }
.box { margin: 1.4em 0; padding: 0.6em 0.8em; border: 1px solid rgba(42,122,114,0.55); background: rgba(42,122,114,0.04); }
.box-title { font-weight: bold; font-size: 1.08em; margin-bottom: 0.5em; }
.box-title .zh { font-weight: normal; font-size: 0.92em; }
.news-intro { font-style: italic; font-size: 0.92em; }
.news-head { font-weight: bold; margin-top: 0.6em; }
.poll { font-size: 0.85em; font-weight: bold; color: #2a7a72; margin: 0.3em 0 0.6em; }
.math { margin: 0.8em 0; text-align: center; }
.math .zh { border: none; padding: 0; font-size: 0.92em; }
.frac { display: inline-block; vertical-align: middle; text-align: center; margin: 0 0.15em; line-height: 1.3; }
.frac > span { display: block; padding: 0 0.25em; }
.frac > span:first-child { border-bottom: 1px solid; }
.grid span { display: inline-block; margin: 0 0.6em; }
.quiz { margin: 1.4em 0; padding: 0.6em 0.8em; border: 1px dashed rgba(107,91,149,0.7); }
.quiz ol, .summary, .answers { padding-left: 1.4em; margin: 0.4em 0; }
.quiz li, .summary li { margin-bottom: 0.7em; }
.hint { font-size: 0.86em; margin-top: 0.25em; }
.mcq { margin: 0.9em 0 1.1em; }
.opts { list-style: none; padding-left: 0.8em; margin: 0.3em 0; }
.opts li { margin: 0.3em 0; }
.to-ans { font-size: 0.8em; text-align: right; }
.answers li { margin-bottom: 0.5em; font-size: 0.92em; }
.gloss dt { margin-top: 0.7em; }
.gloss dt.letter { font-size: 1.2em; font-weight: bold; color: #2a7a72; margin-top: 1.2em; }
.gloss dd { margin: 0.1em 0 0 1em; font-size: 0.9em; }
.principles { border-collapse: collapse; width: 100%; font-size: 0.92em; }
.principles th { text-align: left; padding: 0.5em 0.3em 0.2em; color: #2a7a72; }
.principles td { padding: 0.25em 0.3em; vertical-align: top; border-top: 1px solid rgba(128,128,128,0.3); }
.principles td.k { font-weight: bold; width: 1.5em; }
.rows { padding-left: 1.4em; }
.tbl { overflow-x: auto; margin: 0.6em 0; }
table.grid { border-collapse: collapse; font-size: 0.78em; }
table.grid th, table.grid td { padding: 0.2em 0.5em; border-bottom: 1px solid #ccc; text-align: left; vertical-align: top; }
table.grid th { background: #eef4f3; }
table.grid .zh { font-size: 0.9em; color: #555; }
.titlepage h1 { margin-top: 1.2em; }
.subtitle { font-size: 1.05em; margin: 0.6em 0; }
.legal { font-size: 0.78em; opacity: 0.75; margin-top: 2em; }
a { color: #2a7a72; text-decoration: none; }
table.grid .num { text-align: right; white-space: nowrap; }
"""

# Added to CSS for a paper (academic: ink blue, serif, three-line tables) or a filing (navy, sans serif,
# statement tables, amber analyst notes); the reader page has the matching themes.
KIND_CSS = {
    "paper": """
body { font-family: Charter, "Bitstream Charter", Cambria, Georgia, serif; }
h1, .chap-head { text-align: center; }
h2 { border-top: 1px solid #9aa4b5; font-variant: small-caps; }
.num, .eyebrow, .guide-title, .fig-label, a, .gloss dt.letter { color: #2a4f8f; }
.guide { border-left-color: #2a4f8f; background: rgba(42,79,143,0.06); }
.tag { background: #2a4f8f; }
.note.econ { border-left: 2px solid #2a4f8f; background: transparent; }
.en { text-align: justify; }
table.grid { border-top: 1.5px solid #222; border-bottom: 1.5px solid #222; }
table.grid th { background: transparent; border-bottom: 1px solid #222; }
table.grid td { border-bottom: 0; }
""",
    "filing": """
body { font-family: "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif; }
h2 { border-top: 3px solid #0b4f8a; }
.num, .eyebrow, .guide-title, .fig-label, a, .gloss dt.letter { color: #0b4f8a; }
.guide { border-left-color: #0b4f8a; background: rgba(11,79,138,0.05); }
.tag { background: #0b4f8a; }
.note.econ { border-left: 3px solid #b7791f; background: rgba(183,121,31,0.08); }
.note.econ .tag { background: #b7791f; }
table.grid th { background: #0b4f8a; color: #fff; }
table.grid th .zh { color: #dfe8f2; }
table.grid tr:nth-child(even) td { background: rgba(11,79,138,0.04); }
""",
}


def nav_doc(entries, first, glossary):
    def ol(items):
        return "<ol>" + "".join(f'<li><a href="{h}">{esc(t)}</a>{ol(k) if k else ""}</li>' for t, h, k in items) + "</ol>"
    body = (f'<nav epub:type="toc" id="toc"><h1>目录</h1>{ol(entries)}</nav>'
            '<nav epub:type="landmarks" hidden="hidden"><ol>'
            '<li><a epub:type="titlepage" href="title.xhtml">书名页</a></li>'
            f'<li><a epub:type="bodymatter" href="{first}">正文</a></li>'
            + ('<li><a epub:type="glossary" href="glossary.xhtml">术语索引</a></li>' if glossary else "")
            + '</ol></nav>')
    return page("目录", body)


def ncx_doc(entries, title):
    counter = iter(range(1, 10000))

    def points(items):
        out = ""
        for t, h, k in items:
            n = next(counter)
            out += (f'<navPoint id="np{n}" playOrder="{n}"><navLabel><text>{esc(t)}</text></navLabel>'
                    f'<content src="{h}"/>{points(k)}</navPoint>')
        return out
    return ('<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f'<head><meta name="dtb:uid" content="{BOOK_ID}"/></head><docTitle><text>{esc(title)}</text></docTitle>'
            f"<navMap>{points(entries)}</navMap></ncx>")


def main():
    global BOOK, ROOT, DATA, OUT, BOOK_ID, NOTE, SUBJECT, PAPER, KIND
    sys.stdout.reconfigure(encoding="utf-8")
    BOOK, _ = open_book(sys.argv[1:])
    b = BOOK.cfg["book"]
    ROOT, DATA = BOOK.root, BOOK.data_dir
    OUT = BOOK.export_dir / f'{b["epub_name"]}.epub'
    # a stable identifier, so a reading app treats a re-export as the same book
    BOOK_ID = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, b.get("epub_id") or f'readpdf/{b["epub_name"]}'))
    NOTE, SUBJECT = BOOK.cfg["notes"]["label"], b["subject"]
    KIND = b.get("kind") or "book"
    PAPER = KIND in ("paper", "filing")
    book = load_js(DATA / "book.js", "BOOK_INDEX")
    img = Images()
    chapters = [Chapter(load_js(DATA / f'{c["id"]}.js', "BOOK_DATA["), img) for c in book["chapters"] if c["available"]]
    title = f'{book["title_zh"]}双语精读本'

    docs = {"title.xhtml": title_page(book, chapters)}
    for c in chapters:
        docs[c.file] = c.render()
    gloss, n_terms = glossary_page(chapters)
    toc = [("阅读说明", "title.xhtml", [])] + [c.toc() for c in chapters]
    spine = ["title.xhtml"] + [c.file for c in chapters]
    if n_terms:  # a book without a margin glossary gets no index page
        docs["glossary.xhtml"] = gloss
        toc.append(("全书术语索引", "glossary.xhtml", []))
        spine.append("glossary.xhtml")
    docs["nav.xhtml"] = nav_doc(toc, chapters[0].file, bool(n_terms))

    modified = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
             '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
             '<item id="css" href="style.css" media-type="text/css"/>',
             '<item id="cover" href="img/cover.png" media-type="image/png" properties="cover-image"/>']
    items += [f'<item id="d-{Path(f).stem}" href="{f}" media-type="application/xhtml+xml"/>' for f in spine]
    items += [f'<item id="i-{Path(p).stem}" href="{p}" media-type="{mt}"/>' for p, _, mt in img.files.values()]
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="zh-CN">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{BOOK_ID}</dc:identifier>
<dc:title>{esc(title)}</dc:title>
<dc:creator>{esc(b["author"])}</dc:creator>
<dc:language>zh-CN</dc:language>
<dc:language>en</dc:language>
<dc:description>{esc(b["title"])}：逐句英文原文、中文译文与{esc(SUBJECT)}{NOTE}。个人学习用。</dc:description>
<meta property="dcterms:modified">{modified}</meta>
<meta name="cover" content="cover"/>
</metadata>
<manifest>{"".join(items)}</manifest>
<spine toc="ncx">{"".join(f'<itemref idref="d-{Path(f).stem}"/>' for f in spine)}</spine>
</package>"""

    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0" encoding="utf-8"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   "</rootfiles></container>", compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx_doc(toc, title), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS + KIND_CSS.get(KIND, ""), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/img/cover.png", cover_png(book), compress_type=zipfile.ZIP_STORED)
        for name, text in docs.items():
            z.writestr(f"OEBPS/{name}", text, compress_type=zipfile.ZIP_DEFLATED)
        for path, data, _ in img.files.values():
            z.writestr(f"OEBPS/{path}", data, compress_type=zipfile.ZIP_STORED)
    n_sent = sum(len(p["sentences"]) for c in chapters for p in iter_paras(c.ch))
    print(f"{OUT.relative_to(ROOT)}: {len(chapters)} chapters, {n_sent} sentences, {len(img.files)} images, "
          f"{n_terms} glossary entries, {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
