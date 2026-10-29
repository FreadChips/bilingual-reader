# Automatic PDF engine: layout model + review + hand labels

The default engine for PDF books (`source.engine: auto`, or no `layout.roles` table in book.yaml).
Division of labour: **the program supplies the text, the agent decides the structure.** The text always
comes from the PDF's text layer, so it is exact and the same on every run; the agent never retypes it.
The agent looks at the region map, and records each structural decision as a hand label on a region.

## How it works
1. **Layout model.** The local PyMuPDF layout model (`pymupdf.layout`, ONNX, about 0.1 s a page)
   proposes regions on each page: text, section-header, list-item, caption, picture, table, formula,
   footnote, page-header, page-footer. The boxes are cached in `build/layout.json`, so region ids
   such as `p37.r5` (PDF page 37 as a viewer numbers it, region 5) never shift between runs.
   Extra ids: `p37.x1` is text no region held; `p37.i1` is an image the model missed; `p39.r8m`
   is the margin part of a region the model drew over a margin note and the paragraph beside it.
   To re-run the model on a page, delete its entry from build/layout.json.
2. **Generic roles.**
   - skip: running heads and folios; text repeated near the top or bottom of ≥30% of the chapter's
     pages; rotated text such as photo credits; footnotes (the model's class, or small type at the foot
     of the page that opens with a mark); the chapter title and number.
   - heading: level from the PDF outline, else from type size. A "heading" that starts in lower case or
     ends with a full stop is the tail of a sentence, so it counts as body.
   - The rest: body, list, quote (indented on both sides), margin (outside the main text column),
     caption, picture, formula, dropcap.

   Text clean-up:
   - Ligatures (ﬁ, ﬂ …) are spelt out.
   - A phantom space after a ligature glyph is dropped ("infl ation"), but only when there is no gap
     before the next letter.
   - A star or prime drawn back over the space before it ("r ∗": the italic r's trailing space with the
     ∗ glyph set across it) loses that space, giving r∗, IS∗, P∗. Only ∗, * and ′ trigger it: some PDFs
     (OpenStax) set punctuation and even whole words at zero advance after a space, and closing those
     up broke list numbering and ran words together.
   - Letter-spaced type ("K E Y  C O N C E P T S") is closed up.
   - Typesetting codes left in the text layer ("<h1 The Accumulation of Capital") are dropped; a
     browser would read them as tags and hide the heading.
   - A label split over two text lines ("FIGURE" | "1-2") is joined.
   - A "CHAPTER" label on the opening pages is dropped, even inside another line.
   - Displayed equations stay where they are set, and split the paragraph.
   - Footnote marks (superscript digits right after punctuation, "8 percent.²") are dropped; a
     superscript after a digit, letter or bracket (an exponent) is kept.
   - Smaller type set below or above the line's baseline becomes `<sub>`/`<sup>` (m<sub>t</sub>,
     Q<sup>d</sup>). Patches match with these tags ignored, so older patches keep working. Plain-text
     fields (figure titles and captions, headings) get Unicode instead (S₁, x²), or the bare text.
   - A minus, en dash or macron raised over the glyph before it (an overbar: Ḡ, T̄, M̄/P̄) becomes a combining
     macron, normalized to NFC. Patch matching also normalizes, so a patch may write Ḡ either way.
   - An overbar drawn as a vector stroke (no glyph) also becomes a combining macron: a dark stroke
     at most 1 pt high, centred over one letter, no wider than 1.6× the letter + 1 pt, with its
     lower edge between 0.25 em above and 0.15 em below the letter's top. A glyph just above the
     stroke makes it a fraction bar, and a radical's vinculum sits higher than 0.25 em.
   - Math fonts that store Greek letters and operators as control characters are decoded with
     `layout.glyph_maps` (by glyph name, e.g. `H9266: "π"`) or `layout.font_maps` (by code, one map
     per exact font name). Use glyph_maps when the font has a /Differences encoding: such fonts
     (Mathematical Pi) give the same code a different symbol from page to page, sometimes two
     same-named fonts on one page; the glyph id from the text trace tells them apart. Use font_maps
     for fonts with a fixed encoding (Greek on Latin keys). Look up each symbol in context.
   - A section number drawn apart from its title ("3- What … 3-1 of …") is put back in front of it.
     When there are several candidates, the one the PDF outline confirms wins.
   - A run-in heading set in another typeface at the start of a paragraph ("Used Goods When …") is
     marked bold.
   - A region whose first lines are an outline entry word for word, in one font, followed by a line
     opening in another font, is split: the heading becomes its own region and the rest gets the id
     with a "b" suffix (p173.r5b). The model sometimes boxes a heading line with the paragraph under it.
   - A heading that ends with a full stop is body text ("decades.", "Shut down if TR < VC."), except
     in title case: a news headline such as "Income Inequality Is Not Rising Globally. It's Falling."
   - A label set apart in capitals ("CASE" | "STUDY", KEY CONCEPTS, WORK IT OUT: at most two short
     lines, no punctuation) never runs on into the next paragraph.
   - A picture region the model drew round only the labels of a vector drawing grows over the drawing's
     paths, leaving out frames and tints around it.
3. **Units.**
   - A tinted panel holding one "Figure N / Table N" label and little text (at most 8 lines in body
     type and 1,200 characters in all) is a figure.
   - A longer panel is still a table when it holds one Table label, no picture and at most 8 lines of
     body-type prose (Mankiw's Table 1: twenty propositions in small type). Its title is the text set
     under the label, and its list items become `rows`, translated row by row.
   - Any other panel with text is a box: a short first line is its label, a heading under it its
     title. If the box also holds figures, the figures inside are found separately. A panel is a
     tinted rectangle, a stroked frame (rounded or not) or an image under text.
   - A box panel at the top of a page continues a box that ran to the foot of the previous page.
   - A label outside a panel takes the nearest picture and the caption or margin lines under it. A label
     printed inside a picture also counts, and the lines from the label on are its title and caption.
   - The figure title is the run in its own type after the label ("FIGURE 1.2 **Scarcity of
     Resources** People …"), else the caption's first sentence. The run ends a title only when lighter
     type follows or the next font starts a new line: an italic variable, a symbol or a heavier word
     inside a caption line ("… from *S*₁ to *S*₂", "Training on **ImageNet**") does not end it.
   - Text set flush under the label (same left edge) is the title and caption even when the picture
     box, grown over the drawing, covers it (Mankiw's captions set beside the chart).
   - Other pictures are photos with an adjacent caption. By default, pictures without a caption are
     left out (`auto.uncaptioned_pictures: keep` to keep them).
     A picture with no caption takes a short heading set right above it as its title (Mankiw's
     "Firstbank's Balance Sheet", a one-row table). Pictures under 40 × 30 pt with no text are
     ornaments and are dropped.
   - Features opened by a heading (OpenStax's LINK IT UP, CLEAR IT UP …) need `auto.box_headings`.
     Such a box runs to the first of these: the next full-width horizontal rule, an outline heading, or
     a paragraph ending with an end-of-box mark (■). A rule with footnotes just under it is a footnote
     separator and ends nothing. A panel right under the heading is the box's body.
   - A caption the model drew inside the picture is found as the picture's multi-line text block that
     opens with a bold title.
   - Chart text merged above the label line in the label's region ("Grade" | "FIGURE A-2 …") is
     dropped; the title and caption start at the label.
   - A figure label standing bare ("FIGURE 6-11", no title after it) with no caption outside the
     picture takes a bold-led block of at least 10 words from its picture or from a region right
     beside it (Mankiw sets many captions to the right of the chart). Tables are skipped: the bold
     text inside a table is its header row.
   - Sections named in `auto.skip_sections` (References, Bibliography …) are dropped.
   - With `auto.table_grid`, a table also gets its cells (`grid`), read by tablekit.py from the
     words inside its picture box, decoded like the running text (symbol fonts keep their = and +):
     rows are words on one line; columns are the bands that almost no row writes across (a header
     over two columns does not close the gap), cut where fewest rows write and only where whole
     phrases lie on both sides in enough rows; a phrase over several columns is one cell with a
     span; a body line that only continues the text of the row above is merged into it; header
     rows are those above the first rule, else the leading bold rows, and stacked header lines with
     no spans become one row. Refused: tables with many empty body cells (cells broken over lines),
     rows of a single cell, or symbol-font characters left undecoded. Tested: ResNet 13 of 14
     tables, OpenStax about 4 in 5, Mankiw micro 10 of 19 and macro 16 of 23.

   A PDF without an outline (an article, a paper) is one chapter, and so is any `book.kind: paper`.
   In a paper the first page's title (set large) and the author block before the abstract (or the
   first heading) are dropped.
   - A label followed by a panel in brackets and a lower-case word ("Fig. 6 (middle) shows …",
     "Figure 8.8 (a) presents …") is a sentence, not a label.
4. **Reading order.** XY-cut over the main-column units: split at a column gutter first, then at a
   horizontal gap, recursively. Margin regions stay aside. Margin text that opens in bold becomes a
   glossary entry, attached to the paragraph that bolds the term; other margin text becomes a side note.
5. **Paragraphs.** A region splits at bullets, first-line indents, hanging indents (dialogue, lists) and
   wide gaps. A paragraph continues into the next region (across columns and pages) when it has no
   closing punctuation, or when the next region starts in lower case. Figures and boxes met while a
   paragraph is open wait until it ends.

## Files written by `extract.py N`
| file | use |
| --- | --- |
| `build/source/chNN.json` / `.txt` | the chapter, as with every engine |
| `build/layout/chNN.txt` | every region on every page: id, role, reading position, unit, first words. At the top: **review** notes |
| `build/layout/chNN.json` | the same, for overlay.py |

## Review loop for a chapter
1. `extract.py N`. Read the review summary it prints, then the `## review` list in build/layout/chNN.txt.
2. `overlay.py N --review` renders the flagged pages to build/layout/chNN/pNNNN.png. Open them with the
   image viewing your agent has; an agent that cannot view images can still work from build/layout/chNN.txt. `overlay.py N` renders every page, and `overlay.py N 41 42` renders chosen pages.
   - Frame colours: body blue, heading red, margin orange, caption teal, picture green, quote purple,
     skip grey; dashed grey = tinted panel.
   - Thick frames mark units: figure green, box orange, photo teal.
   - The tag on each region is "r5 body #3": region, role, reading position.
3. Also read build/source/chNN.txt as usual (merged paragraphs, wrong joins, stray words).
4. Fix the problems, preferring them in this order:
   - a **hand label** in `labels/chNN.yaml` for structure: a role, a box, an order, a join;
   - an **`auto.rules` rule** in book.yaml when the same fix recurs across chapters (≈3+ times);
   - a **patch** in `patches/chNN.json` for words the PDF encodes badly. Match the text as the .txt
     shows it, including `<b>`/`<i>` tags.
5. Re-extract. Check that the review list is down to accepted items, and score against a snapshot if one exists.

Review notes are hints, not errors: "heading not in the PDF outline" is often a box title or an
end-of-chapter heading; "outline entry not found" is often a box whose title sits in the box itself.

## labels/chNN.yaml
```yaml
p41.r7: skip                      # shorthand for {role: skip}
p40.r3: {role: heading, level: 1}
p52.r4: {role: margin}            # roles: skip body list quote heading margin caption picture formula
p44.r2-r11: {box: case1}          # a range of regions on one page; regions sharing a box name form one box,
p45.r1-r6: {box: case1}           #   across pages too
p44.r3: {box: case1, label: CASE STUDY}   # this region is the box's label (its text is replaced by the value)
p60.r2: {join: true}              # continues the previous paragraph (false: never joins)
p56.r9: {after: p56.r7}           # read right after the unit holding that region (prefer this to order:
p61.r5: {order: 2.5}              #   numeric positions shift when another label changes a role)
p70.i1: {label: Figure 4}         # this picture is Figure 4
p338.r5: {keep: true}             # keep this uncaptioned picture as a photo (a small unnumbered data table)
p383.r2: {continue: tab12-2}      # this picture is the rest of table 12-2 (continued on a later page):
                                  #   it is stacked under the table's image instead of becoming a photo
p73.r9: {join: true}              # on a panel box's first region: the box continues the previous box
p7.r2: {grid: false}              # on a table's label or picture: keep it as an image, no grid
p441.r4: {join: true}             # on a heading right after another heading: its second line, set apart
                                  #   ("An Alternative Theory:" | "The Imperfect-Information Model")
```
Later keys override earlier ones, and dict values merge, so a range can be followed by a single-region
refinement. A label naming a region that does not exist stops the extraction.

## auto section of book.yaml (all optional)
| key | default | meaning |
| --- | --- | --- |
| `rules` | [] | region rules, first match wins: `role` (+ `level`) and conditions on the region's first line (as in layout.roles), plus `cls` (model class), `in_margin`, `in_panel`, `lines_max` |
| `figure_label` | `^(Figure|Fig\.|Table|Exhibit|Chart|Map|Graph)\s*(A-1, 3, 3.2 …)` | case-insensitive; group 1 word, group 2 number |
| `heading_number` | `^((?:\d+[.\-])+\d*[a-z]?|\d+)\s+(.*)` | section number in headings |
| `footnotes` | skip | skip / body |
| `skip_sections` | References, Bibliography, Works Cited, Notes, Index | headings whose section is dropped |
| `box_headings` | [] | regexes for feature headings that open a box (`['LINK IT UP', 'CLEAR IT UP', 'WORK IT OUT']`) |
| `uncaptioned_pictures` | skip | skip / keep |
| `table_grid` | false (new books: true) | tables as cells: tablekit.py reads rows and columns from the words in the table's picture box; a table it cannot read stays an image with a review note |
| `caption_position` | nearest | nearest / below / above: a label outside a panel takes the picture above it (`below`: the caption sits under its figure, as in papers) or under it, falling back to the nearest |
| `furniture_share` | 0.3 | share of pages a repeated top/bottom text must reach to be dropped |
| `margin_overlap` | 0.3 | a region overlapping the main column by less than this share is margin |
| `box_label_max` | 40 | longest first line taken as a box label |

`layout.font_maps` (this engine only): `{exact font name: {code: character}}`. Write control
characters as YAML escapes, e.g. `MathematicalPi-One: {"\x03": "=", "\x06": "α"}`.
`layout.glyph_maps` (this engine only): `{glyph name: character}`, applied to every font whose
encoding names that glyph. `pdf_glyphs.py BOOK.pdf [--names H9266,…] [--first N --last M]` lists
each glyph name with up to three example lines in which it is shown as «name».
The `layout` keys `symbol_fonts`, `symbol_map`, `bold_markers`, `italic_markers` and `page_offset`
apply to this engine too. Printed page numbers come from the PDF's page labels when it has them.

## Measuring
`score.py OTHER_DIR [chapters] [--show N]` compares build/source with another extraction: sentence
recall/precision and counts of headings, figures, boxes, glossary terms, photos. Use it after changing
rules (against `build/snapshot` from `regress.py save`), or to compare engines.

First test (Mankiw, Principles of Microeconomics, 22 chapters), scored against the hand-tuned rule
engine with patches:

| | blank rule profile | automatic engine, no rules |
| --- | --- | --- |
| sentences found | 72.7% | 97.5% |
| precision, body text | 39.5% (whole chapter) | 91.2% (before the summary) |
| headings / figures / glossary | 0 / 0 / 0 | all found / 158 of 158 / 170 of 172 |

Chapter 1 reached 100% with four labels, one patch and one rule.

Two more layouts, with no reference to score against, were checked by reading the output and overlays:
- **OpenStax, Principles of Economics 3e** (995 pages, 34 chapters, 98 s).
  - One book-specific line in book.yaml (`box_headings`).
  - Every chapter, section, figure and table was found, with titles split from captions.
  - 282 of the 285 feature boxes came out right, including WORK IT OUT panels that spill over pages.
  - 0.3% of the 16,644 sentences start in lower case. Most of them are the end-of-chapter Key Terms
    lists (bold term + definition, no full stop), which run together.
- **arXiv 1512.03385** (ResNet; 12 pages, two columns, no outline, 4 s). Made into a full study
  edition on 2026-10-29: 2 labels, 14 patches, 377 sentences.
  - Reading order across columns is right, and sentences continue from column to column.
  - All 21 figures and tables were found with captions.
  - Heading levels came from type size.
  - Displayed equations became math blocks.
  - Footnotes and the reference list were dropped.
  - The `font_profile.txt` symbol-font guess was wrong here (CMR7 subscripts are real digits).
    Check a guess against a page before copying it.

**Full run on a new book: Mankiw, *Macroeconomics*** (682 pages, 20 chapters, 64 s).
- book.yaml needed one engine setting, `box_headings: ['CASE STUDY']`, plus the title and source note.
- 10,892 sentences and 194 figures/tables. Figure titles were split from their captions, FYI boxes
  were found by their frames, and CASE STUDY boxes by their heading.
- Chapter 1 went through the whole pipeline:
  - three hand labels for the end-of-chapter page;
  - 285 sentences annotated, with 49 notes;
  - `build.py`: 0 problems;
  - reader: 285 sentences, 6 figures, 3 boxes, 4 equations;
  - EPUB: `check_epub` found 0 problems.
- Chapters 2–4 went the same way (601, 664 and 437 sentences; 0 build problems each). Each needed
  a few hand labels for two-column end-of-chapter lists and some `math_match` patches for fractions.
  Chapter 4's balance sheets, small tables titled by a heading above them, led to the
  heading-as-title rule above.
- Chapter 5 (688 sentences) brought in the italic Mathematical Pi fonts, whose codes change page to
  page; `glyph_maps` replaced the per-font code tables and decodes all 20 chapters with no control
  characters left. Sub/superscript detection came from its Cagan-model appendix (m<sub>t</sub>,
  p<sub>t+1</sub>); its stacked fractions (A2–A9) still needed `math_match` patches.
- Chapter 6 (733 sentences, 25 figures and tables) had six figures with no title: the caption sat
  beside the chart, inside the picture region or in a region the grown picture covered. The
  bare-label rule above found all six; a `figure` patch fixed one table title and one caption.
- Chapter 11 (358 sentences) set exogenous values with overbars drawn as a separate raised minus
  (Ī, Ḡ, T̄); the overbar rule above came from it. Its "Mathematical note" footnotes run on with
  displayed equations and body-size lines the footnote rule misses: label those regions `skip`.
- Chapter 12 (552 sentences) drew some overbars with an en dash ("Y–"), now decoded too; two older
  patches written against the dash form became redundant. Table 12-2 runs over two pages; the
  `continue` label above came from it.
- Chapter 14 (592 sentences) drew natural output Ȳ as Y plus a vector stroke; the vector-overbar
  rule above came from it and also fills in Ȳ, C̄ and Ī in chapters 15–17. Its two-line heading
  led to the heading `join` label, and the auto test book's Figure A-2 crash to the
  chart-text-above-label rule.
- New generic gaps this book exposed, all now handled by the engine:
  - labels split over two lines;
  - phantom spaces after ligatures;
  - letter-spaced headings;
  - framed boxes;
  - equations pulled out of their paragraph;
  - "George H. W. Bush" split after the initials.

What it still gets wrong, so check for these:
- boxes without a tinted panel (Mankiw's Case Study), and banners such as Quick Quiz;
- labels set beside the text they belong to: "CASE STUDY" now stands as its own paragraph, but when
  the model puts the "STUDY" line inside the paragraph's region it lands mid-sentence ("being private
  STUDY goods");
- a table the model reads as text (ResNet Table 10): label its region `{role: picture}`;
- in a short document, line-end hyphens in compounds are joined ("highlevel"): the vocabulary is too
  small to tell compounds from split words, so patch them;
- footnotes are dropped: in a paper, give their content in the note on the sentence with the marker;
- end-of-chapter quizzes, which become headings and paragraphs, not a quiz;
- equations stored as digits, when `symbol_fonts` is not set;
- cartoon captions with no detected picture, which become side notes;
- glossary lists set as bold term + definition with no full stop (they run together into one sentence);
- author names and affiliations on a paper's first page, which come out as a paragraph (label them skip);
- an equation number printed before the equation in the math block.
