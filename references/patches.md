# patches/chNN.json — hand fixes to the extracted text

A list of patches applied in order after parsing, before paragraph numbering. Each patch finds the
first paragraph whose sentences (joined with spaces, inline HTML included) contain `match`, so patches
survive renumbering. A patch that matches nothing stops the extraction with an error.

| op | effect |
| --- | --- |
| `"sentences": [...]` | replace the paragraph's sentences (fix a bad split, join a split name) |
| `"replace_first"` / `"replace_last"` | replace the first / last sentence |
| `"fix": [[old, new], ...]` | string replacements in every sentence (typos, run-in drop caps, "oneunit") |
| `"type": "bullets" \| "quote" \| "para"` | change the paragraph type |
| `"parts": [{"s": [...], "type"?}, {"math": "html"}, ...]` | rebuild one paragraph as several paragraphs and displayed equations (run-in lists, equations inside text) |
| `"math": "html"` | replace the paragraph by a displayed equation |
| `"then_math": "html"` (+ `"then_sentences"`) | insert an equation (and an optional paragraph) after it |
| `"delete": true` | remove the paragraph |
| `"move_after": "fragment"` | move the paragraph after the paragraph containing the fragment (two boxes interleaved on a page) |
| `"box": {field: value}` | set fields of the enclosing box (title read as body text, a false author line) |
| `"math_match": "fragment"` + `"math": "html"` or `"delete": true` | find a displayed equation (not a paragraph) by its HTML and rewrite or drop it: fractions set on two lines, an equation split in two |
| `"figure": "fig6-7"` + `"title"`, `"caption"`, `"source"` | set a figure's or table's fields (a title run into its caption, chart labels caught in the caption) |

Patches run in file order, and matching ignores `<sub>`/`<sup>` tags. A `delete` of a split-off
fragment goes before the patch that rebuilds the whole paragraph, or it may match the rebuilt text.

Equation HTML may use `<i>`, `<sub>`, `<sup>` and fractions:
`<span class="frac"><span>numerator</span><span>denominator</span></span>`.

Order matters: put a `delete` before a rewrite that would create the same matching text.
Rule of thumb: a problem seen three or more times belongs in the profile, not in patches.

Example:
```json
[
  {"match": "George H. W.", "sentences": ["… George H. W. Bush, told me …", "If President Obama …"]},
  {"match": "we can write this condition for optimization as",
   "then_math": "<span class=\"frac\"><span><i>MU</i><sub>X</sub></span><span><i>MU</i><sub>Y</sub></span></span> = …"},
  {"match": "The idea of a backward-sloping labor-supply curve", "box": {"title": "INCOME EFFECTS ON LABOR SUPPLY"}}
]
```
