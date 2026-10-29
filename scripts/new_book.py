"""Set up a book folder for the bilingual-reader toolkit.

Usage: python new_book.py DIR SOURCE.pdf|SOURCE.epub [--subject 经济学]

Copies the source into DIR, creates the folder layout, the reader page, a draft book.yaml and
NOTES.md (status and hand-over notes for whichever agent works on the book next).
For an EPUB the draft is usually complete (title, author, chapter pattern from the TOC).
For a PDF it also writes font_profile.txt (fonts, page geometry, fonts storing math symbols as
digits); the automatic engine needs no layout rules (references/auto-layout.md).
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # run with python -I, which leaves out the script's folder


def guess_chapter_pattern(labels):
    """Pick the TOC/outline pattern that numbers the most entries."""
    candidates = [r"^Chapter (\d+)[:.]?\s*(.*)", r"^CHAPTER (\d+)[:.]?\s*(.*)", r"^(\d+)[.:]?\s+(.*)",
                  r"^Part (\d+)[:.]?\s*(.*)", r"^Lecture (\d+)[:.]?\s*(.*)"]
    best = max(candidates, key=lambda p: len({re.match(p, l).group(1) for l in labels if re.match(p, l)}))
    return best if any(re.match(best, l) for l in labels) else candidates[0]


def yaml_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    subject = "经济学"
    if "--subject" in args:
        i = args.index("--subject")
        subject = args[i + 1]
        del args[i:i + 2]
    root, src = Path(args[0]).resolve(), Path(args[1]).resolve()
    kind = src.suffix.lower().lstrip(".")
    if kind not in ("pdf", "epub"):
        sys.exit("the source must be a .pdf or .epub file")
    if (root / "book.yaml").exists():
        sys.exit(f"{root / 'book.yaml'} already exists")
    for d in ("labels", "patches", "annotations", "build/source", "reader/data", "reader/img", "export"):
        (root / d).mkdir(parents=True, exist_ok=True)
    if src.parent != root:
        shutil.copy2(src, root / src.name)
    shutil.copy2(HERE.parent / "templates" / "reader.html", root / "reader" / "index.html")

    title, author, labels = src.stem, "", []
    if kind == "epub":
        from extract_epub import Package
        pkg = Package(root / src.name)
        title, author = pkg.title or title, pkg.author
        labels = [e[0] for e in pkg.toc]
    else:
        import pymupdf
        doc = pymupdf.open(root / src.name)
        meta = doc.metadata or {}
        title, author = meta.get("title") or title, meta.get("author") or ""
        labels = [t for _, t, _ in doc.get_toc()]
        report = subprocess.run([sys.executable, "-I", str(HERE / "font_profile.py"), str(root / src.name)],
                                capture_output=True, text=True, encoding="utf-8")
        (root / "font_profile.txt").write_text(report.stdout + report.stderr, encoding="utf-8")
    pattern = guess_chapter_pattern(labels) if labels else ""  # no outline: the whole PDF is one chapter
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:60] or "book"

    template = (HERE.parent / "templates" / f"book-{kind}.yaml").read_text(encoding="utf-8")
    text = (template.replace("{{title}}", yaml_str(title)).replace("{{author}}", yaml_str(author))
            .replace("{{subject}}", subject).replace("{{file}}", yaml_str(src.name))
            .replace("{{pattern}}", yaml_str(pattern)).replace("{{slug}}", slug))
    (root / "book.yaml").write_text(text, encoding="utf-8")
    notes = (HERE.parent / "templates" / "NOTES.md").read_text(encoding="utf-8")
    (root / "NOTES.md").write_text(notes.replace("{{title}}", title), encoding="utf-8")

    print(f"book folder: {root}")
    print(f"source: {src.name} ({kind}), title: {title!r}, author: {author!r}")
    if pattern:
        matched = [l for l in labels if re.match(pattern, l)]
        numbers = {re.match(pattern, l).group(1) for l in matched}
        print(f"chapter pattern {pattern!r} matches {len(matched)} of {len(labels)} TOC entries "
              f"({len(numbers)} chapter numbers; the first entry of each number is used), e.g.:")
        for l in matched[:5]:
            print(f"  {l}")
    else:
        print("no outline: the whole document is one chapter (chapter 1); fill in book.title")
    if kind == "pdf":
        print("next: copy any symbol fonts from font_profile.txt into layout.symbol_fonts; no other layout rules are needed")
    print("fill in book.yaml's annotation: section with the user before chapter 1")
    print("then: python extract.py --book", root, "1")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
