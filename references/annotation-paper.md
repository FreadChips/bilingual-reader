# Annotating a paper (`book.kind: paper`)

Read annotation-guide.md first: the file format, the build and the checks are the same. This page
covers what differs for a research paper.

## Set-up
- `new_book.py DIR paper.pdf --kind paper --subject 深度学习` makes the whole paper one chapter
  (the outline lists its sections, not chapters), sets `auto.caption_position: below` (papers put
  captions under figures and tables) and drafts the annotation standard. Fill in `book.title` (the
  PDF metadata is often empty), `author`, `title_zh`, `source_note`.
- The extraction drops the first page's title and author block, footnotes, and the reference list.
  Before annotating, print the reference list from the PDF (it is needed to name cited works) and
  the footnotes (their markers stay in the text as `<sup>n</sup>`).
- A whole paper is one round: extract → labels/patches → annotate → build → check → reader → EPUB
  → hand over. Write the paras files in parts (abstract and introduction; method; experiments;
  appendix) and run quality.py / check.py after each part.

## Standard (agree it with the user; ResNet, 2026-10-29)
About 25 notes per 100 sentences, covering four things:
1. **Problem and contribution** — what was unsolved, what this paper adds, what exactly is new
   compared with the closest earlier work. Put this on the abstract, the introduction's claims and
   the conclusion.
2. **Method** — the key equations and design choices: what each symbol is, the intuition, why it
   is built this way and not another (e.g. why learning a residual is easier than the full mapping),
   what it costs (parameters, FLOPs).
3. **Reading the experiments** — for every table and curve: what is compared, which number to look
   at, what the gap means, whether the comparison is fair (same parameters? same training?).
   **Open the image before quoting a number.** Tables come out as images; quote the key cells in
   the figure note so the reader can follow without zooming.
4. **Context and later impact** — name the cited work behind each important `[n]` (from the
   reference list: "[41] 是 VGG（Simonyan & Zisserman, ICLR 2015）"), and say what came later only
   when it is well established (a follow-up paper by the same authors, a design that became
   standard). Never invent citations, numbers or dates; when unsure, describe the idea without them.

## Writing
- Terms: on first use write 中文（English）, e.g. 残差学习（residual learning）, 恒等映射（identity
  mapping）; afterwards use the Chinese term or the usual English abbreviation (BN, SGD, FLOPs, mAP).
  Keep model and dataset names in English (ResNet-34, ImageNet, CIFAR-10).
- Keep citation numbers `[41]` and figure/table references (Fig. 4, Table 3) as they are in the
  translation.
- Symbols in translations and notes are plain text with Unicode (F(x), Wₛ, 3×3, 10³); bold vectors
  lose their bold, which is fine.
- **Footnotes**: the text keeps their markers. On the sentence that carries `<sup>n</sup>`, give the
  footnote's content in the note ("脚注3：作者把训练迭代次数加到3倍仍看到退化……").
- **Typos in the original** ("expect that" for "except that"): translate the intended meaning and
  point the slip out in the note only when it could confuse.
- Section cross-references: "第3.2节", "附录A". There are no chapters; quality.py's 第N章 check does
  not apply.
- The guide (`meta.json` "guide") has four short paragraphs: the problem, the core idea, the main
  results with numbers, and why the paper matters / what to read next.
- Headings: translate every heading id in the .txt, including the appendix ones (hA1 …).
- Run-in paragraph headings ("**Residual Representations.**") are their own sentence: translate
  them as **残差表示。**

## Checks specific to papers
- Every number in a note matches the table image or the text.
- Every named citation matches the reference list.
- The guide's claims match the abstract and the conclusion.
