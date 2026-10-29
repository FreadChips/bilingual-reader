---
name: bilingual-reader
description: Turn an English book, research paper or financial report (PDF, EPUB, or an HTML filing such as a 10-K) into a sentence-by-sentence bilingual study edition — original sentence, Chinese translation, subject notes, figures in place — with a browser reader and a phone EPUB. Use when the user wants a book made into a 双语精读本 / 逐句对照 / 中英对照学习版, wants the next chapter of such a book, or wants to fix, check or export one.
---

# Bilingual study edition of a book

Output: `reader/index.html` (interactive reader: show/cover/hide translations, notes toggles, quizzes,
remembered position) and `export/<name>.epub` (offline reading on a phone). Every sentence of the book
gets a translation; important sentences get a subject note (`n`) and occasionally a language note (`l`).

The toolkit lives in this skill's `scripts/` folder. It works with any coding agent that can run
Python and read files (Claude Code, Codex …); everything an agent needs is in this folder and in the
book folder, nothing in an agent's private memory. The folder is its own git repository (see
README.md): commit toolkit changes there.

Run every script with the book folder as working directory (or pass `--book DIR`) and with
`python -I`. Python 3.10+ needs PyMuPDF 2.x with its layout model, and PyYAML. If the project has no
environment, create one with `python -m venv .venv`, then run
`.venv/Scripts/pip install -r <this folder>/requirements.txt` (about 100 MB with onnxruntime;
installing downloads packages, so ask the user first). `python -I -c "import pymupdf.layout"` checks it.
Tested with pymupdf 2.0 / pymupdf-layout 2.0 on Windows.
Below, `K` is this skill's `scripts` folder.

## Ground rules
- **Start every session with `python -I K/status.py`** in the book folder: one line per chapter
  (extracted, labels, patches, annotated paragraphs, note density, check result, NOTES.md status) and
  the next step. Do that step; do not start a chapter while an earlier one awaits the user's approval.
- **Read `NOTES.md` in the book folder first, and update it when you finish.** It holds the status of
  every chapter, the chapter outline (for cross-references), and hand-over notes for the next chapter.
  Another agent or session may continue where you stop, and NOTES.md is all it gets.
- **Copyright.** Books are almost always copyrighted. Keep everything local and on the user's own devices;
  never publish the reader, the EPUB or extracted text as a hosted page or artifact. Put a
  `source_note` (仅供个人学习使用，请勿传播) in book.yaml.
- **One chapter per round.** Extract → check the .txt → patch → annotate → build (0 problems) → check in
  the reader → hand over for approval. The user approves a chapter before the next one.
- **Agree the standard before chapter 1** and write it into the `annotation:` section of book.yaml:
  what the notes focus on, how many notes per 100 sentences (the Mankiw book drifted from 28 to 4 and
  back; `quality.py` flags this), when a language note is worth writing (the reader's English level).
  Follow that section on every chapter; change it only when the user does.
- **Never change extraction rules without the regression check** (step 4).

## 1. Set up the book folder
```
python -I K/new_book.py BOOKDIR path/to/book.(pdf|epub) --subject 经济学
python -I K/new_book.py PAPERDIR path/to/paper.pdf --kind paper --subject 深度学习
```
A **paper** (`book.kind: paper`) is one chapter and one round; read `references/annotation-paper.md`
before annotating it (its own standard, footnotes, citations, set-up steps).
A **financial report** (`--kind filing`, an SEC 10-K saved as HTML) has its Items as chapters
(Item 7A inside chapter 7) and its tables as grids from the HTML cells; set `chapters.include` to
the Items agreed with the user and read `references/annotation-filing.md` (numbers check, terms).
Creates the layout (book.yaml, NOTES.md, labels/, patches/, annotations/, build/, reader/, export/),
copies the reader page, guesses the chapter pattern from the TOC and prints which entries it matched. Prefer an
EPUB source whenever one exists, since its structure is already marked up. A scanned PDF (no text
layer) needs OCR first; say so and stop.

Fill in `book.title_zh`, `source_note`, the `annotation:` section (agreed with the user), and check
`chapters.*_pattern` matched exactly the chapters. Write the chapter list into NOTES.md.

## 2. PDF only: the automatic engine
PDFs go through the automatic engine by default. It uses a local layout model, generic rules and
your hand labels, so no font rules need writing (`references/auto-layout.md`). The program supplies
the text from the PDF's text layer, and you decide the structure by looking at the region map. Never
retype the book's text.
From `font_profile.txt` copy only the fonts that store math symbols as digits (`layout.symbol_fonts`,
`symbol_map`). If the source shows control characters where symbols should be, list the math fonts'
glyph names with `pdf_glyphs.py BOOK.pdf` and write `layout.glyph_maps`. The old rule engine (`source.engine: rules`, a `layout.roles` table,
`references/profile.md`, `references/example-mankiw.yaml`) is still available for a series whose
rules already exist.

## 3. Extract and check a chapter
```
python -I K/extract.py 3          # → build/source/ch03.json + ch03.txt (+ images in reader/img)
python -I K/overlay.py 3 --review # PDF: region map of the flagged pages → build/layout/ch03/p*.png
```
PDF: read the review list at the top of `build/layout/ch03.txt`. Open the overlay images with
whatever image viewing your agent has (an agent that cannot view images works from
build/layout/ch03.txt alone) and fix structure with hand labels in `labels/ch03.yaml`: a role, a box, a reading order, a
paragraph join. The review loop and the label format are in `references/auto-layout.md`. Render a few
unflagged pages too (`overlay.py 3 41 42`); not every problem is flagged.

Then read `ch03.txt` top to bottom. Look for:
- run-in headings or lists merged into one paragraph;
- displayed equations garbled inside a paragraph;
- sentences split wrongly (initials, abbreviations);
- drop caps;
- boxes cut or interleaved across pages;
- captions swallowing body text, or figures missing;
- end-of-chapter quiz numbering.

Tables read as cells show as `grid:` lines (`H|` = header row): compare each with its image
(reader/img); turn a wrong one off with `{grid: false}` on its label region.

Fix wording the source encodes badly with a patch (`patches/ch03.json`, ops in
`references/patches.md`). Fix anything that recurs (≈3+ times) with a rule in book.yaml instead
(`auto.rules` for PDFs).

## 4. Regression check after any rule or toolkit change
A change to `scripts/` affects every book, so check all of them:
```
python -I K/regress_all.py save      # before the change: re-extracts every book in testbed.yaml, snapshots it
python -I K/regress_all.py compare   # after it: re-extracts again and lists every changed sentence
```
`testbed.yaml` (in this skill folder) lists the annotated books and the test books (other layouts).
Exit status 0 = no changes, 1 = a chapter crashed or was not rewritten, 2 = changes: read the log
in `logs/`. Only the intended chapters may change; a shifted paragraph id breaks that chapter's
annotations. A change to one book's book.yaml rules, patches or labels needs only that book:
```
python -I K/regress.py save      # before the change
python -I K/extract.py all       # after it (a chapter that fails is reported, the others still run)
python -I K/regress.py compare   # every changed sentence, id and figure position
```
`score.py build/snapshot` summarises the same comparison as percentages and counts. Hand labels on
an already annotated chapter need this check too.

## 5. Annotate
Write `annotations/ch03/meta.json` and `paras-1.json`, `paras-2.json`, … keyed by the ids in the .txt.
Format, writing rules and checks: `references/annotation-guide.md`. Essentials:
- one translation per source sentence, same order (build fails otherwise);
- notes explain the subject (concepts, logic, how to read the figure, links to other chapters);
- open the figure image (reader/img/…) before stating any number from it;
- check every "第N章" cross-reference against what that chapter actually covers.
Large chapters: split paragraphs across several paras-*.json files.

## 6. Build and check
```
python -I K/build.py 3           # must end with "0 problems"
python -I K/check.py 3           # must end with "ch03: ok"
```
check.py needs no browser. It checks the annotation files' shape, that every sentence has a Chinese
translation, writing slips (single-asterisk italics, unbalanced bold, HTML, wrong 第N章), the note
density against `annotation.notes_per_100`, and that reader/data matches the source and the
annotations and has the shape the reader expects.
Open the reader (serve `reader/` with `python -m http.server`, or your agent's browser preview) at
`#ch03`, reload, and confirm counts of `.sent`, `.fig-card`, `.box`, `.mcq` and that the page text has
no "undefined". If your agent has no browser, say so in the hand-over so the user checks the page.
Update NOTES.md (status line, outline, hand-over notes), then hand the chapter to the user.

## 7. Finish the book
```
python -I K/quality.py           # note density per chapter, glossary consistency, leftover markup, bad 第N章
python -I K/export_epub.py       # → export/<epub_name>.epub
python -I K/check_epub.py export/<epub_name>.epub
```
Tell the user how to get the EPUB onto a phone (Apple Books, 微信读书 import, Android readers,
Send to Kindle) and to keep it private.

## References
- `references/auto-layout.md` — automatic PDF engine: region ids, review loop, overlay, hand labels, auto.rules, score.py
- `references/profile.md` — book.yaml schema: book fields, PDF rule-engine layout rules, EPUB mapping
- `references/example-mankiw.yaml` — full profile of a complex textbook PDF (Mankiw, Principles 8e)
- `references/annotation-guide.md` — annotation file format, writing standard, checks
- `references/annotation-paper.md` — what differs for a research paper (`book.kind: paper`)
- `references/annotation-filing.md` — what differs for a financial report (`book.kind: filing`)
- `references/patches.md` — hand-patch operations
- `references/lessons.md` — what went wrong on the first book and how it was fixed
