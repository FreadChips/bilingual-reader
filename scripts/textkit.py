"""Book-independent text handling: inline markup, line joining with hyphen repair, sentence splitting."""
import re


def span_markup(span, bold=("Bold", "Bd", "Black"), italic=("Italic", "Obl")):
    """Wrap a PDF span in <b>/<i> from its font name."""
    text = span["text"]
    font = span["font"]
    if not text.strip():
        return text
    if any(m in font for m in bold):
        return f"<b>{text}</b>"
    if any(m in font for m in italic):
        return f"<i>{text}</i>"
    return text


def clean_markup(s):
    s = s.replace("­", "-").replace("■", "").replace("●", "")
    s = re.sub(r"</b>(\s*)<b>", r"\1", s)
    s = re.sub(r"</i>(\s*)<i>", r"\1", s)
    s = re.sub(r"<(b|i)>(\s*)</\1>", r"\2", s)
    s = re.sub(r"<(b|i)>(\s+)", r"\2<\1>", s)
    s = re.sub(r"(\s+)</(b|i)>", r"</\2>\1", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ([,.;:?!])", r"\1", s)
    s = re.sub(r"“\s+", "“", s)
    # the PDF sometimes sets a compound with a stray space ("well- being"); keep "x- and y-axis"
    s = re.sub(r"\b([A-Za-z0-9]+)- (?!(?:and|or|to)\b)([a-z])", r"\1-\2", s)
    return s.strip()


_SUP = str.maketrans("0123456789+−-=()niad", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱᵃᵈ")
_SUB = str.maketrans("0123456789+−-=()aeiorstx", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑᵢₒᵣₛₜₓ")


def plain_scripts(s):
    """Sub/superscripts in plain text (titles, captions): Unicode characters where every character
    has one (S<sub>1</sub> -> S₁), else the bare text."""
    def conv(m, table):
        inner = re.sub(r"</?(b|i)>", "", m.group(1))
        out = inner.translate(table)
        return out if all(a != b or a == " " for a, b in zip(inner, out)) else inner
    s = re.sub(r"<sub>(.*?)</sub>", lambda m: conv(m, _SUB), s)
    return re.sub(r"<sup>(.*?)</sup>", lambda m: conv(m, _SUP), s)


def strip_tags(s):
    return re.sub(r"</?(b|i)>", "", plain_scripts(s))


class Joiner:
    """Join PDF lines into running text, repairing hyphenation."""

    def __init__(self, vocab):
        self.vocab = vocab

    def join(self, parts):
        out = ""
        for part in parts:
            part = part.replace("­", "-").strip()
            if not part:
                continue
            if not out:
                out = part
                continue
            m = re.search(r"([A-Za-z]+)-(</[bi]>)?$", out)
            n = re.match(r"(<[bi]>)?([a-z][A-Za-z]*)", part)
            if m and n:
                a, b = m.group(1), n.group(2)
                joined, hyph = (a + b).lower(), f"{a}-{b}".lower()
                # keep the hyphen in a genuine compound such as trade-offs, or inside a hyphenated
                # chain such as supply-and-demand split after "and-"
                if self.vocab[hyph] > self.vocab[joined] or (
                        out[m.start(1) - 1:m.start(1)] == "-" and not self.vocab[joined]):
                    out = out + part
                else:
                    out = out[: m.start(1)] + a + (m.group(2) or "") + part
                    out = out.replace(a + "</b><b>", a).replace(a + "</i><i>", a)
                continue
            if out.endswith(("—", "–")) or part.startswith(("—", "–")):
                out = out + part
            else:
                out = out + " " + part
        return clean_markup(out)


ABBREV = {
    "u.s", "e.g", "i.e", "mr", "mrs", "ms", "dr", "st", "inc", "co", "corp", "jr", "sr",
    "vs", "etc", "no", "p", "pp", "fig", "a.m", "p.m", "u.k", "u.n", "ph.d", "vol", "ed",
}


def split_sentences(html):
    """Split a paragraph (with <b>/<i> markup) into sentences."""
    text = html
    boundaries = []
    pat = re.compile(r"([.?!])((?:</[bi]>)?[\"”’)]*(?:</[bi]>)?)\s+(?=(?:<[bi]>)?[\"“‘(]?[A-Z0-9$])")
    for m in pat.finditer(text):
        end = m.end(2)
        before = strip_tags(text[: m.start(1)])
        word = re.search(r"(?:^|[\s(])([A-Za-z.]+)$", before)
        w = word.group(1) if word else ""
        if m.group(1) == ".":
            if re.fullmatch(r"\s*\d+", before):  # the number of a list item
                continue
            if w.lower() in ABBREV:
                continue
            # an ellipsis ("...") runs on; four dots are a full stop plus an ellipsis
            dots = len(re.search(r"\.*$", before).group(0)) + 1
            if dots in (2, 3):
                continue
            if re.search(r"\. \.$", before.rstrip()) or text[m.start(1) - 2: m.start(1)] == ". ":
                continue
            if len(w) == 1:
                # an initial ("Robert D. McTeer", "George H. W. Bush") follows a capitalised name or
                # another initial; "point A." ends a sentence
                prev = re.search(r"([A-Za-z]+)\.?\s+[A-Za-z]$", before)
                if w.islower() or (prev and prev.group(1)[0].isupper()):
                    continue
                # ... or comes first, before a first name and surname ("and N. Gregory Mankiw")
                if re.match(r"\s*[A-Z][a-z]+ [A-Z][a-z]+", text[end:]):
                    continue
        boundaries.append(end)
    out, last = [], 0
    for b in boundaries:
        out.append(text[last:b].strip())
        last = b
    out.append(text[last:].strip())
    # an italic run may span sentences; close it at each sentence end and reopen it in the next
    balanced, carry = [], []
    for s in (s for s in out if s):
        s = "".join(f"<{t}>" for t in carry) + s
        carry = [t for t in "bi" if s.count(f"<{t}>") > s.count(f"</{t}>")]
        balanced.append(s + "".join(f"</{t}>" for t in reversed(carry)))
    return balanced
