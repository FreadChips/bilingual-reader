"""Shared plumbing for the bilingual-reader toolkit: the book folder, its profile and line rules.

A book folder looks like this (new_book.py creates it):

    book.yaml            profile: titles, source file, layout rules (see references/profile.md)
    <source>.pdf|.epub   the book itself
    patches/chNN.json    hand fixes for the extracted text
    annotations/chNN/    translations and notes (meta.json, paras-*.json)
    build/source/        extracted chapters (chNN.json for the build, chNN.txt to read)
    reader/              index.html, data/*.js, img/*
    export/              the EPUB

Every script takes the book folder from --book DIR, else the BOOK_DIR environment variable,
else the current directory.
"""
import os
import re
import sys
from pathlib import Path

import yaml

DEFAULTS = {
    "book": {"title": "", "title_zh": "", "author": "", "subject": "", "source_note": "",
             "epub_name": "bilingual-reader"},
    "source": {"type": "pdf", "file": ""},
    "chapters": {"outline_pattern": r"^Chapter (\d+):\s*(.*)", "titles_zh": {}},
    "notes": {"label": "解读"},
}


def as_list(v):
    return v if isinstance(v, (list, tuple)) else [v]


def merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


class Book:
    def __init__(self, root):
        self.root = Path(root).resolve()
        path = self.root / "book.yaml"
        if not path.exists():
            sys.exit(f"no book.yaml in {self.root} (pass --book DIR or run new_book.py first)")
        self.cfg = merge(DEFAULTS, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        self.source = self.root / self.cfg["source"]["file"]
        self.src_dir = self.root / "build" / "source"
        self.img_dir = self.root / "reader" / "img"
        self.data_dir = self.root / "reader" / "data"
        self.ann_dir = self.root / "annotations"
        self.patch_dir = self.root / "patches"
        self.export_dir = self.root / "export"

    def title_zh(self, num):
        """Chinese chapter title: the chapter's meta.json wins over the profile's list."""
        meta = self.ann_dir / f"ch{num:02d}" / "meta.json"
        if meta.exists():
            import json
            t = json.loads(meta.read_text(encoding="utf-8")).get("title_zh")
            if t:
                return t
        titles = self.cfg["chapters"].get("titles_zh") or {}
        return titles.get(num) or titles.get(str(num)) or ""


def open_book(argv):
    """Pop --book DIR from argv and return (Book, remaining args)."""
    args = list(argv)
    root = os.environ.get("BOOK_DIR") or os.getcwd()
    if "--book" in args:
        i = args.index("--book")
        root = args[i + 1]
        del args[i:i + 2]
    return Book(root), args


# ---------------------------------------------------------------- line rules
# A rule is a mapping of conditions that must all hold, or {"any": [rule, ...]}.
# Used for the PDF role table (layout.roles) and for every other font test in a profile.

def _text(line):
    return line["text"].strip()


CONDITIONS = {
    "font": lambda l, v: l["font"].startswith(tuple(as_list(v))),          # font name prefix
    "font_not": lambda l, v: not l["font"].startswith(tuple(as_list(v))),
    "font_in": lambda l, v: l["font"] in as_list(v),                         # exact font name
    "font_has": lambda l, v: any(x in l["font"] for x in as_list(v)),        # substring
    "font_not_has": lambda l, v: not any(x in l["font"] for x in as_list(v)),
    "font_ends": lambda l, v: l["font"].endswith(tuple(as_list(v))),
    "size_min": lambda l, v: l["size"] >= v,
    "size_max": lambda l, v: l["size"] <= v,
    "size_gt": lambda l, v: l["size"] > v,
    "size_lt": lambda l, v: l["size"] < v,
    "size_eq": lambda l, v: l["size"] == v,
    "text_max_len": lambda l, v: len(_text(l)) <= v,
    "text_starts": lambda l, v: _text(l).startswith(tuple(as_list(v))),
    "text_starts_ci": lambda l, v: _text(l).upper().startswith(tuple(x.upper() for x in as_list(v))),
    "text_contains": lambda l, v: any(x in _text(l) for x in as_list(v)),
    "text_eq": lambda l, v: _text(l) in as_list(v),
    "text_regex": lambda l, v: re.search(v, _text(l)) is not None,
    "y_outside": lambda l, v: l["bbox"][1] < v[0] or l["bbox"][1] > v[1],   # running heads, folios
    "y_within": lambda l, v: v[0] <= l["bbox"][1] <= v[1],
}


def rule_matches(line, rule, extra=None):
    """extra: conditions supplied by the caller that need page context (e.g. in_margin)."""
    if rule is None:
        return False
    if "any" in rule:
        return any(rule_matches(line, r, extra) for r in rule["any"])
    for key, val in rule.items():
        if key in ("role", "note"):
            continue
        if extra and key in extra:
            ok = extra[key](line) == val
        elif key in CONDITIONS:
            ok = CONDITIONS[key](line, val)
        else:
            raise ValueError(f"unknown rule condition: {key}")
        if not ok:
            return False
    return True
