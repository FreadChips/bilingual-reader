# Annotations: format, writing standard, checks

Annotations live in `annotations/chNN/`. `build.py` merges them with `build/source/chNN.json` and
refuses to finish (non-zero exit, "N problems") while anything is missing or misaligned.

## paras-*.json — the sentences
One object per paragraph id from the .txt dump (`p01`, `p02`, … including paragraphs inside boxes,
bullets and quotes). Split a long chapter over `paras-1.json`, `paras-2.json`, …; ids must not repeat.

```json
{
  "p01": {
    "s": [
      "简单句子只写译文。",
      {"t": "需要说明的句子写成对象。", "n": "学科解读：概念、机制、读图方法、与其他章的联系。", "l": "word：语言提示。"}
    ],
    "sum": "可选：一句话段落要点。"
  }
}
```
- `s` has exactly one entry per source sentence, in order. A bullet item counts as one sentence.
- `t` translation, `n` subject note, `l` language note, all optional except the translation.
- Markdown: only `**bold**`. No HTML in translations; write subscripts as plain text (Q₁, P_X) and
  use a plain `<` in notes, not `&lt;`.
- Run-in headings that open a paragraph ("**Perfect Substitutes** Suppose…") keep the bold:
  "**完全替代品**　假设……".

## meta.json — everything else
| key | content |
| --- | --- |
| `title_zh` | chapter title |
| `guide` | 本章导读: a list of 2–4 paragraph strings (markdown **bold**); a single string is split at blank lines |
| `headings` | `{heading id: 中文标题}` for every heading in the .txt (`h3-2`, `hA1` …) |
| `boxes` | `{box id: {label, title, intro, headline, byline, author, source}}` — translate the fields the box has |
| `figures` / `tables` | `{fig1: {title, caption, n, rows, cells}}` — `n` is the figure note; `rows` one per source row; `cells` for a table with a grid (below) |
| `photos` | `{img01: {zh: caption translation, n: optional note}}` |
| `glossary` | `{term in lower case: {zh, def}}` for the chapter's key terms (margin definitions) |
| `quickquiz` | `{qq01: [{zh, hint}, …]}` one per quiz item; `hint` is 参考思路 |
| `experts` | `{ask01: {title, propositions: [...], n}}` |
| `math` | `{eq01: translated equation HTML}` when an equation contains words |
| `end` | `{quiz: {cq1: {stem, options: {a..d}, answer, explain}}, summary: {sum1: …}}` |
| `appendix_guide` | guide paragraphs for an appendix |

**Tables as cells.** A table the engine read as a grid (`grid:` lines in the .txt, `H|` marks header
rows) needs `cells`: one entry per distinct cell text that holds an English word in lower case,
`{"top-1 err.": "top-1 错误率", "18 layers": "18 层"}`. Numbers, symbols and names such as "ResNet-34"
need none; a name with a lower-case word ("BN-inception [16]") may map to itself. The reader shows
each cell's translation under it, and keeps the table image under "查看原书表格". build.py and
check.py list missing and stale cells. Read the grid against the image before translating; if it
is wrong, turn it off with the hand label `{grid: false}` on the table's label or picture region.

Glossary keys are lower case even when the term is capitalised in the book (build lower-cases terms);
keep curly apostrophes as in the source (`arrow’s impossibility theorem`).

## Writing standard
- Translate every sentence faithfully and naturally; keep the book's terminology consistent across
  chapters (quality.py lists terms translated two ways).
- **Notes are about the subject.** Put one on sentences that carry a concept, a mechanism, a common
  misconception, a worked number, or a link to another chapter. Explain why, not just what; show the
  arithmetic behind numbers. Agree the density with the user before chapter 1 and hold it
  (Mankiw: roughly 10–25 notes per 100 sentences; quality.py flags chapters far below the median).
- **Language notes are rare**: only words or grammar a reader at the agreed level would stumble on.
- Figure notes say how to read the figure and quote its key numbers — **open the image first**.
- Cross-references ("第19章讲过……") must name what that chapter really covers; when unsure, describe
  the idea without the chapter number.
- Translate quiz questions and give a 参考思路 for each; end-of-chapter quiz answers with a one-line
  explanation.

## Checks before handing a chapter over
1. `build.py N` → 0 problems; `check.py N` → ok (shape, Chinese in every translation, writing
   slips, density against book.yaml `annotation.notes_per_100`, reader data current).
2. Reader: reload `#chNN`; count sentences, figures, boxes, quiz items; no "undefined" on the page;
   look at one figure and one box.
3. Numbers in notes match the images; cross-references hold.
