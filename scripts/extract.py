"""Extract chapters from the book's source (PDF or EPUB) into build/source/chNN.json and .txt.

Usage: python extract.py [--book DIR] 3          one chapter
       python extract.py [--book DIR] 3 4 5      several
       python extract.py [--book DIR] all        every chapter the source has

The .txt dump is what you read to check the extraction and to write annotations against.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with python -I, which leaves out the script's folder
from bookkit import open_book  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    book, args = open_book(sys.argv[1:])
    if not args:
        sys.exit(__doc__)
    nums = [] if args == ["all"] else [int(a) for a in args]
    kind = book.cfg["source"]["type"]
    # PDF: the automatic engine unless the profile asks for the rule engine (or has a role table)
    engine_name = book.cfg["source"].get("engine") or ("rules" if (book.cfg.get("layout") or {}).get("roles") else "auto")
    if kind == "pdf" and engine_name == "auto":
        import extract_auto as engine
    elif kind == "pdf":
        import extract_pdf as engine
    elif kind == "epub":
        import extract_epub as engine
    else:
        sys.exit(f"source.type must be pdf or epub, not {kind!r}")
    engine.extract(book, nums)


if __name__ == "__main__":
    main()
