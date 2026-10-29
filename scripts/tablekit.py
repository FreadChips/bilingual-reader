"""Tables as cells: rows and columns from the positions of the words inside a table's picture box.

The text comes from the PDF's text layer word by word, so numbers are exact. Rows are words sharing
a line; columns are the horizontal bands that (almost) no row writes across, so a header spanning
two columns ("COCO train" over "@.5 | @[.5, .95]") does not hide the gap between them. A phrase
that covers several columns becomes one cell with a span. Header rows are the rows above the first
horizontal rule inside the table, else a first row with no numbers over rows with numbers.

A grid that does not look like a table (many empty cells, cells broken over several lines, symbol
fonts) is refused with a reason; the table then stays an image.
"""
import re

LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})
NUMERIC = re.compile(r"[\s\d.,:%±+\-−–—×()\[\]†‡*/<>=~$€£¥]*")


def is_numeric(text):
    return bool(text.strip()) and NUMERIC.fullmatch(text) is not None


def needs_translation(text):
    """A cell is translated when it holds an English word in lower case ("plain", "top-1 err.")."""
    return re.search(r"\b[a-z]{3,}\b", re.sub(r"<[^>]+>", "", text or "")) is not None


def build_grid(words, rect, rules=()):
    """words: [(x0, y0, x1, y1, text[, bold]), ...] inside rect; rules: y of horizontal rules across the table.
    Returns ({"header": h, "rows": [[cell, ...], ...]}, None) or (None, reason).
    A cell is a string, or {"t": text, "span": n} when it covers n columns."""
    words = [(w[0], w[1], w[2], w[3], w[4].translate(LIGATURES), bool(w[5]) if len(w) > 5 else False)
             for w in words if w[4].strip()]
    if len(words) < 4:
        return None, "too few words"
    if any(re.search(r"[\x00-\x1f-]", w[4]) for w in words):
        return None, "symbol-font characters"
    hs = sorted(w[3] - w[1] for w in words)
    h = hs[len(hs) // 2]
    # rows: words whose vertical centres are close
    rows = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        c = (w[1] + w[3]) / 2
        if rows and abs(rows[-1]["c"] - c) <= 0.45 * h:
            rows[-1]["w"].append(w)
        else:
            rows.append({"c": c, "w": [w]})
    if len(rows) < 2:
        return None, "one row"
    # phrases: words of a row separated by no more than a wide word space
    for r in rows:
        ph = []
        for w in sorted(r["w"], key=lambda w: w[0]):
            if ph and w[0] - ph[-1]["x1"] < 0.4 * h:
                ph[-1]["x1"], ph[-1]["t"] = max(ph[-1]["x1"], w[2]), ph[-1]["t"] + " " + w[4]
            else:
                ph.append({"x0": w[0], "x1": w[2], "t": w[4]})
        r["ph"] = ph
    # columns: bands that at most a quarter of the rows (spanning headers) write across
    x0, x1 = min(w[0] for w in words), max(w[2] for w in words)
    step = 0.5
    n = int((x1 - x0) / step) + 1
    cover = [0] * n
    for r in rows:
        seen = [False] * n
        for p in r["ph"]:
            for i in range(int((p["x0"] - x0) / step), min(n, int((p["x1"] - x0) / step) + 1)):
                seen[i] = True
        for i, s in enumerate(seen):
            cover[i] += s
    limit = max(1, int(0.25 * len(rows)))
    bounds, i = [x0], 0
    while i < n:
        if cover[i] <= limit and 0 < i:
            j = i
            while j < n and cover[j] <= limit:
                j += 1
            if j < n and (j - i) * step >= 0.4 * h:
                # cut where fewest rows write: past a long row label, not through it
                low = min(cover[i:j])
                k = i
                while cover[k] != low:
                    k += 1
                m = k
                while m < j and cover[m] == low:
                    m += 1
                cut = x0 + (k + m) / 2 * step
                # whole phrases on both sides in enough rows: a gap, not a ragged edge of one column
                left = sum(1 for r in rows if any(p["x1"] <= cut for p in r["ph"]))
                right = sum(1 for r in rows if any(p["x0"] >= cut for p in r["ph"]))
                if min(left, right) >= 0.3 * len(rows):
                    bounds.append(cut)
            i = j
        else:
            i += 1
    bounds.append(x1 + 1)
    ncol = len(bounds) - 1
    if ncol < 2:
        return None, "one column"
    if ncol > 30:
        return None, f"{ncol} columns"
    laid = []
    for r in rows:
        cells = [""] * ncol
        ends = list(range(ncol))  # last column each cell covers
        placed = set()
        for p in r["ph"]:
            cols = [k for k in range(ncol) if p["x0"] < bounds[k + 1] and p["x1"] > bounds[k]] or [0]
            # a phrase starts in the column holding most of its left part
            k = next(c for c in cols if p["x0"] < bounds[c + 1])
            cells[k] = (cells[k] + " " + p["t"]).strip()
            ends[k] = max(ends[k], cols[-1])
            placed.add(k)
        starts = sorted(placed)
        for a, b in zip(starts, starts[1:] + [ncol]):
            ends[a] = min(ends[a], b - 1)  # a span never covers a column with text of its own
        laid.append({"cells": cells, "ends": ends, "placed": placed, "c": r["c"],
                     "bold": all(w[5] for w in r["w"])})
    # header: rows above the first rule that has rows on both sides
    header = 0
    for y in sorted(rules):
        above = sum(1 for r in laid if r["c"] < y)
        if 0 < above < len(laid):
            header = above
            break
    if not header:  # no rule: the leading rows set in bold
        k = 0
        while k < min(3, len(laid) - 1) and laid[k]["bold"]:
            k += 1
        if 0 < k < len(laid) and not all(r["bold"] for r in laid):
            header = k
    # a body cell wrapped onto the next line ("Represents a movement along the" | "curve"): that line
    # only has text under text of the row above, starting in lower case or after an unfinished line
    merged = laid[:max(header, 1)]
    for r in laid[max(header, 1):]:
        prev = merged[-1]
        filled = [k for k in r["placed"] if r["cells"][k]]
        if (filled and len(filled) < len(prev["placed"])
                and all(prev["cells"][k] and not is_numeric(prev["cells"][k]) and not is_numeric(r["cells"][k])
                        and (r["cells"][k][:1].islower() or not re.search(r"[.:;!?)]$", prev["cells"][k]))
                        for k in filled)):
            for k in filled:
                prev["cells"][k] = prev["cells"][k].rstrip("-") + ("" if prev["cells"][k].endswith("-") else " ") + r["cells"][k]
            continue
        merged.append(r)
    grid = []
    for r in merged:
        row, k = [], 0
        while k < ncol:
            span = r["ends"][k] - k + 1 if k in r["placed"] else 1
            row.append(r["cells"][k] if span == 1 else {"t": r["cells"][k], "span": span})
            k += span
        grid.append(row)
    rows = merged
    if not header:
        texts = [[c if isinstance(c, str) else c["t"] for c in row] for row in grid]
        if not any(is_numeric(c) for c in texts[0]) and any(is_numeric(c) for row in texts[1:] for c in row):
            header = 1
    if header >= len(rows):
        header = 0
    # quality: a body row should fill most of its columns
    body = grid[header:]
    width = lambda row: sum(1 if isinstance(c, str) else c["span"] for c in row)  # noqa: E731
    empty = sum(sum(1 for c in row if isinstance(c, str) and not c) for row in body)
    total = sum(width(row) for row in body)
    if total and empty / total > 0.3:
        return None, f"{empty} of {total} body cells empty (cells broken over lines?)"
    thin = sum(1 for row in body if sum(1 for c in row if (c if isinstance(c, str) else c["t"])) == 1)
    if ncol >= 3 and thin > 0.3 * len(body):
        return None, "rows with a single cell (cells broken over lines?)"
    return {"header": header, "rows": grid}, None
