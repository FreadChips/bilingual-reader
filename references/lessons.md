# Lessons from the first book (Mankiw, Principles of Microeconomics 8e, 22 chapters, 8,666 sentences)

Effort: chapter 1 plus extractor tuning took most of a day; chapters 2–12 the same day; 13–22 the next
day. Once extraction is stable a chapter is one round of extract → check → annotate → build. 88 patches
were needed over 17 chapters; the extraction rules changed about ten times.

## Extraction
- **Figures belong after the paragraph that first mentions them**, not where the page put them
  (the user asked for this; `place_figures` does it for "Figure N" / "Table N" mentions).
- **Position rules over-reach.** A caption rule for "short line under a picture" captured body text
  beside pictures in six chapters, and later a column of article text that resumed under a photo. Each
  position rule now has an exclusion (italic only; not when the same font runs straight on into the
  next block). Every new rule → regression check of all finished chapters.
- **PDF text is not the text.** Operators stored as digits (= as 5), drop caps split from their word,
  boxes interleaved across pages, run-in lists merged into one paragraph, "IN COME" split words,
  "George H. W. / Bush" split at an initial. Read every chapter's .txt.
- **Hyphenation**: decide by the book's own vocabulary (job-counting stays, eco-nomics joins); a
  hyphen chain such as supply-and-demand split after "and-" keeps its hyphen.
- **Italics spanning sentences** must be closed and reopened per sentence.
- **Patch order**: a delete must run before a rewrite that produces the same matching text.

## Automatic PDF engine (added after the first book)
- With a blank rule profile, only 72.7% of the book's sentences came out right, and no headings,
  figures or margin notes at all. The layout model with generic rules found 97.5% of the sentences,
  158 of 158 figures and 170 of 172 glossary notes, without one book-specific rule. Hand rules are now
  for what remains, not the starting point.
- The model draws one region over a margin note and the paragraph beside it now and then: split by
  the main column, but only lines that sit beside the others (a short last line is not a margin note).
- Chapter-title tests must be strict: "economics" is a substring of "Ten Principles of Economics".
- Text over a picture the model missed (a header band, the page footer) must not join that picture.
- Region ids are stable only because the model's boxes are cached in build/layout.json; keep the cache
  with the book. Re-running the model on the same page gave identical output.
- Tested on two more layouts (an OpenStax textbook and a two-column arXiv paper). Each one exposed a few
  generic gaps:
  - ligatures;
  - an answer key that repeats the chapter numbers in the outline;
  - no outline at all;
  - footnotes;
  - the reference list;
  - a model "heading" that is the tail of a sentence;
  - panels that hold body text and several figures;
  - boxes that spill onto the next page.

  Fix such gaps in the engine, not with labels, whenever the problem is not book-specific.
- Hand labels that give a numeric reading `order` broke when another label added a region to the
  page, because the positions shifted. Use `after: <region id>`.
- The chapter guide must be a list of paragraphs. A plain string passed build.py and then crashed the
  reader (ch.guide.map). build.py now splits a string at blank lines. Always open the reader after
  building; build's "0 problems" does not prove the page renders.
- Matching headings to the outline by prefix was too loose: "The GDP deflator reflects …" matched the
  outline entry "The GDP Deflator". A prefix match now needs similar lengths. An entry may also end with
  the heading ("FYI Stocks and Flows"), because the label is printed apart.
- A box opened by a heading must not close on its own title, even when the title is an outline entry.
- Notes thinned out on the second chapter of a book (12.5 against 17.2 per 100 sentences) until
  quality.py was run; run it after every chapter, not every few.
- Note density drifted down again on the third macro chapter (12.0 per 100 sentences). The cause is
  writing three files in one go and checking only at the end. Run quality.py as soon as the files
  exist, and top up before handing over.
- Annotations must not use single-asterisk *italics*; only **bold** renders. The habit slipped back
  every chapter, so strip them before building with `strip_italics.py annotations/chNN` (`--dry`
  lists the matches first). A naive *…* pattern also eats stars used as symbols (k*, r₁* … r₂*,
  P*/P) and silently corrupted two macro chapters; the script only removes a pair that wraps
  Chinese text and does not follow a letter, digit or bracket.
- Math-heavy chapters need most of their patches for equations: overbars (K̄) printed as a separate
  "_" line (a raised minus overlapping the letter is now decoded by the engine), exponents flattened onto the baseline, fractions split over two lines. Budget for 30–40
  patches in a chapter like Mankiw's chapter 3; the text itself needs few.
- A text-layer fix that looks generic can be wrong elsewhere. Dropping every space that the next
  glyph overlaps fixed Mankiw's "r ∗" but merged OpenStax question numbers ("1 ." set at zero
  advance) and would have glued words in the Principles PDF. Probe a new rule over every test PDF
  (where does it fire, on which characters?) before the regression run, and narrow it to the case
  you saw (macro ch. 13: only a star or prime over the space).
- Regression backups taken from build/source can be stale for chapters nobody re-extracted after
  an earlier rule change (macro ch. 14–18 still lacked the WWDOC font maps). Isolate a new rule by
  extracting the same chapters with it switched off and on.
- `extract.py all` stops at the first chapter that raises, and `regress.py compare` only sees the
  files that were rewritten, so a crash hides every later chapter: "no changes" can mean "never
  ran". The auto test book crashed at ch. 2 (a figure label region opening with a chart word) for
  several rounds while its regression read clean. Count tracebacks in every regression log.
- The italics stripper's guard ("no star right after an ASCII letter") missed Greek symbols: π*, πₜ*
  read as an italic opening and would have lost their stars. The guard now covers Latin, Greek,
  combining marks, sub/superscripts and primes, but not CJK (Chinese is what real italics wrap).
  check.py uses the same pattern.
- A crash used to stop `extract.py all`; now each chapter runs on its own and the failures are
  listed at the end with a non-zero exit, and regress_all.py also flags any chapter file a run did
  not rewrite.
- A test book's drift went unreviewed for two days (Oct 27–29) while every regression read clean,
  because each round only compared with the round before. Scoring both versions against the
  hand-tuned book found a net gain with five regressions hidden in it, one of which lost text (a
  side caption under a grown picture box). Each came from a rule written for another book: a
  1,200-character box limit (macro FYI boxes) that broke a long text table, a title rule that cut at
  any font change, a heading-tail rule that fired on a whole region. Score against a reference, not
  only against the last snapshot, after a run of engine changes.
- Fix engine rules in a git worktree and test on copies when another agent is working on a book:
  the skill is linked into every agent, so an edit in place changes its next extraction at once.
- PyMuPDF's find_tables read a LaTeX table's numbers as "2807 ." and scrambled letters; the words
  of the text layer are exact, so tablekit.py builds the grid from word positions itself. Raw
  words still dropped "=" and "+" set in Mathematical Pi ("Exports Imports"): table words must go
  through the same decoding as the running text (pdf_layout.page_words). Check a few grids per
  book against the image, including one with symbols.
- Column cuts: the midpoint of a low-coverage band can fall inside a long row label and swallow the
  number next to it (ResNet Table 9 lost 41.5); cut where fewest rows write, never let a span cover
  a column with text of its own, and require whole phrases on both sides of a cut.
- Probe a new rule's geometry before trusting its thresholds. Mankiw's vector overbars sit within
  0.21 em of the glyph top; a radical's vinculum (2√k) and stray strokes in OpenStax and arXiv sat
  0.31–0.38 em above it, so the window is 0.25 em. Expect a few misses in running text all the same
  (macro ch. 14: three Ȳ patched by hand).
- Horizontal rules: table rows are drawn as rules too, so only full-width rules (≥60% of the page)
  end a heading-opened box.
- A patch that splits or merges paragraphs renumbers every paragraph after it. Finish labels and
  patches, rebuild the source, and only then write paras files against the final ids (macro ch. 4:
  one split and one paragraph turned into math shifted p41–p56).
- A font's name does not identify its encoding. Mathematical Pi subsets reuse one code for Δ on one
  page and + on the next, and a page can carry two same-named fonts. Map by glyph name
  (`glyph_maps`), and check a decoded symbol against the rendered page before trusting it.
- Patches run in file order. A `delete` matched on a fragment can also hit a paragraph an earlier
  patch just rebuilt; put deletes of split-off fragments before the patch that rebuilds the whole.
- Notes and translations are escaped as text: write x², mₜ, pₜ₊₁-style Unicode, never `<sup>` or
  `<sub>` (quality.py flags them). Only the English source and `math` entries carry HTML.
- Composed and combining accents differ as strings ("Ḡ" vs "G" + U+0304). The engine and patch
  matching both normalize to NFC; any new matching code should do the same.
- Note density fell to 10.5 per 100 sentences on macro ch. 6 (a long, model-heavy chapter written
  in three files), and needed 48 extra notes; ch. 7's first draft was at 12.5. First drafts land
  low every time. Budget about one note per six sentences while writing, and run quality.py
  before the reader check either way.
- Small worked tables (bank balance sheets) come out as pictures. Their numbers belong in the photo
  note, so the reader can follow the arithmetic without zooming.

## Annotation
- **Density drifted**: 28 notes per 100 sentences in chapter 1, 4–5 in chapters 13–19, 12–19 in
  20–22. Agree a standard first and run quality.py every few chapters.
- **Numbers from figures were wrong** several times until the rule "open the image before writing a
  number" was followed.
- **Cross-references were wrong** (a chapter cited for a model it does not contain). Check them.
- **Glossary keys** must be lower case; build lower-cases terms and silently misses capitalised keys.
- Language notes: the user has a reasonable English base; ~70 per 370 sentences was the upper bound.

## Delivery
- The reader keeps the reading position and settings per book (`storage_key`).
- The EPUB is the "read it anywhere" version: no scripts, answers collected after the quiz, a
  book-wide glossary index; reading apps zoom figures. Validate with check_epub.py.
- Never publish copyrighted text; keep the outputs on the user's devices.
