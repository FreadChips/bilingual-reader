# book.yaml — the book profile

Everything specific to one book lives here; the scripts hold no book-specific values.

## book
| key | meaning |
| --- | --- |
| `kind` | `book` (default), `paper` or `filing` (an HTML financial report; see annotation-filing.md). A paper or a filing shows no chapter numbers, and each kind has its own look in the reader and the EPUB: book (warm paper, teal, serif cards), paper (white page, ink blue, Charter-like serif, centred title with authors, three-line tables, side-rule notes), filing (cool grey, navy, sans serif with tabular figures, navy table headers, banded rows, bold totals, red negatives, amber analyst notes). For `paper`: a paper is one chapter, the reader and EPUB show no chapter numbers, and its annotation standard is in annotation-paper.md |
| `title`, `title_zh`, `author` | shown in the reader, the EPUB title page and metadata |
| `subject` | 经济学, 心理学 …; notes are labelled `<subject><notes.label>` (经济学解读) |
| `source_note` | HTML line under every chapter and on the EPUB title page: source and 仅供个人学习使用，请勿传播 |
| `epub_name` | file name of the EPUB in export/ |
| `epub_id` | optional seed of the EPUB identifier (keep it fixed so re-exports replace the same book in a reading app) |
| `storage_key` | optional browser-storage prefix of the reader (defaults to epub_name) |

`notes.label` (default 解读). `cover` (optional): `title`, `subtitle`, `english` lines of the generated cover.
The cover's CJK font sets Latin digits at full width: write 第八版, not 第8版.

## annotation
The annotation standard agreed with the user before chapter 1. Every agent that writes annotations
for the book reads it, so the standard does not depend on one agent's memory.
| key | meaning |
| --- | --- |
| `focus` | what the subject notes explain (经济学优先：概念、机制、常见误解、读图、章节联系) |
| `notes_per_100` | target subject notes per 100 sentences; check each chapter against it with quality.py |
| `lang_notes` | when a language note is worth writing, i.e. the reader's English level |
| `other` | anything else agreed: paragraph summaries, quiz answers, fixed translations of terms |

## NOTES.md
Next to book.yaml (new_book.py creates it from templates/NOTES.md). Status of every chapter, the
chapter outline used to check "第N章" cross-references, hand-over notes for the next chapter, and a
short log of toolkit changes made for this book. Read it before starting; update it after each chapter.

## html (`source.type: html`, a filing; all optional)
| key | default | meaning |
| --- | --- | --- |
| `item_pattern` | `^Item\s+(\d+)([A-Z]?)\.\s*(.*)$` | a bold line matching it opens an Item: number, letter, title; a lettered Item goes into the chapter of its number |
| `furniture` | running footers "… \| 2025 Form 10-K \| 20", bare page numbers, PART headings | lines dropped |
| `heading_max` | 160 | longest bold line taken as a heading |

## source
`type: pdf | epub | html`, `file:` relative to the book folder.
`engine` (PDF): `auto` (default; layout model + hand labels, see auto-layout.md and its `auto:` section)
or `rules` (the font/position rule engine below). Without `engine`, a profile with a `layout.roles`
table uses the rule engine.

## chapters
- `outline_pattern` (PDF): regex on PDF outline entries; group 1 = chapter number, group 2 = title.
  A chapter runs to the next outline entry at the same or a higher level.
- `toc_pattern` (EPUB): regex on TOC labels, same groups. Chapters are cut at the TOC anchor and run to
  the next TOC entry at the same or a higher level, so front and back matter fall away.
- `include`: optional list of the chapters agreed to be done (a filing: `[7, 8]`); status.py lists only these.
- `titles_zh`: optional `{number: 中文标题}`; a chapter's meta.json `title_zh` wins. Chapters listed
  here appear in the reader's chapter list before they are built.

## epub (EPUB sources; all optional)
| key | default | meaning |
| --- | --- | --- |
| `heading_map` | h1: title, h2: 1, h3–h6: 2 | `title` = the chapter title (dropped), 1/2 = section levels |
| `box_tags` | [aside] | elements that become boxes (their first heading is the box title) |
| `box_classes` | sidebar, box, boxed, feature, callout, note-box | classes that become boxes |
| `skip_classes`, `skip_epub_types` | page breaks, footnotes, endnotes, noterefs, toc | dropped |
| `figure_label_pattern` | `^(Figure|Fig\.|Table|Exhibit)\s*([A-Z]?[\d.\-]+)` | a `<figure>` whose caption matches is a numbered figure/table, placed after the paragraph that first mentions it; other images are photos |
| `heading_number` | `^((?:\d+[.\-])+\d*|\d+)\s+(.*)` | section number in headings |

Tables become `rows` ("cell | cell"). EPUB text has no page numbers; the reader and EPUB omit them.

## PDF layout
The PDF engine (scripts/extract_pdf.py) reads each line's font, size and position, gives it a role
from the `layout.roles` table, rebuilds reading order and assembles paragraphs, boxes, figures,
quizzes and end matter. Features you do not configure stay off.

### layout
| key | meaning |
| --- | --- |
| `page_offset` | PDF page index − printed page number (font_profile.txt prints it) |
| `pages` | geometry keyed by printed-page parity `even` / `odd`, or `all`: `main_left` (x of the body text's left edge), `margin` (`x1_max` and/or `x0_min`: where margin notes sit) |
| `body_font` | prefix of the body font; used for displayed quotations and to see where a box is interrupted |
| `box_font` | substring of the font set inside side boxes |
| `box_quote_marker` | a box block set wholly in a font containing this is a quotation (e.g. Obl) |
| `end_mark_font` | font of the end-of-box mark (e.g. ZapfDingbats ■) |
| `symbol_fonts`, `symbol_map` | fonts that store operators as digits, and the digit→symbol map |
| `bold_markers`, `italic_markers` | substrings of font names that mean <b> / <i> (defaults Bold/Bd/Black, Italic/Obl) |
| `heading_number` | regex splitting "3-2a Title" into number and text |
| `roles` | ordered rules; first match wins; unmatched = body |

### Roles
`skip` (running heads, folios, credits, text inside graphs), `dropcap`, `chapter_title`, `h1`, `h2`,
`label` (box labels FYI / Case Study, figure labels FIGURE 3, Quick Quiz banner, end-matter headers),
`box_title`, `news_intro`, `news_headline`, `news_byline`, `source`, `margin_def` (margin glossary
note), `caption`, `quiz`, `body`.

### Rule conditions (all must hold; `any: [rule, …]` for alternatives)
| condition | test |
| --- | --- |
| `font` / `font_not` | font name starts with (one of) |
| `font_in` | font name is exactly (one of) |
| `font_has` / `font_not_has` | font name contains |
| `font_ends` | font name ends with |
| `size_min` `size_max` `size_gt` `size_lt` `size_eq` | font size (rounded to 0.1) |
| `text_starts`, `text_starts_ci`, `text_contains`, `text_eq`, `text_regex`, `text_max_len` | the stripped line text |
| `y_outside: [top, bottom]`, `y_within` | the line's top y |
| `in_margin: true/false` | inside the page's `margin` area |
`role` and `note` are not conditions.

### Other PDF sections (rules use the same conditions)
- `captions.photo_italic_font`: a lone line in this font under a wide photo is its caption.
- `figures`: `label_pattern` (on the upper-cased label line; groups kind and number), `title`,
  `caption`, `rows`, `source` rules for the lines inside the figure panel.
- `quick_quiz`: `label` (the banner text), `continue` (rule for lines that continue the quiz).
- `experts`: `label`, `proposition` rule, `percent_font` (an expert-poll panel; Mankiw-specific).
- `margin_glossary.term_marker`: font substring of the bold term opening a margin definition.
- `end_matter`: `banner` rule where end-of-chapter material starts, `fallback_start` text, `sections`
  (`key` quiz / summary / …, `contains` or `starts`). The quiz is parsed as numbered stems with a.–d.
- `appendix.title`: rule for the title line where an appendix starts.

### Writing the layout section
1. From font_profile.txt: the biggest share is the body font; headings are the larger, rarer sizes;
   lines at the very top/bottom in a small font are running heads and folios (skip).
2. Start with skip, chapter_title, h1, h2. Extract one chapter; read the .txt; look up the font of any
   misplaced line with a quick PyMuPDF dump of that page (`page.get_text("dict")`).
3. Add boxes, captions, figures, margin notes and end matter one at a time, each followed by an
   extraction of two or three chapters that have them.
4. Prefer narrow rules (font + size + position) and give every rule that looks at position an
   exclusion case; broad caption/margin rules swallow body text.
