# Annotating a financial report (`book.kind: filing`)

Read annotation-guide.md first: the file format, the build and the checks are the same. This page
covers what differs for an annual report such as an SEC Form 10-K.

## Set-up
- Source: the filing as one HTML file (an SEC 10-K, or a copy of it). `new_book.py DIR filing.html
  --kind filing` writes a profile with `source.type: html`. Chapters are the Items: Item 7 (with 7A)
  is chapter 7, Item 8 chapter 8. Agree with the user which Items to do; MD&A (Item 7) and the
  financial statements with their notes (Item 8) are the core.
- Fill in `book.title_zh` (e.g. 苹果公司 2025 财年年度报告（10-K）), `author` (the company) and the
  `source_note`. Public filings are not copyrighted like books, but keep the outputs local unless
  the user decides otherwise.
- The tables come straight from the HTML cells: every number is exact. Read each grid in the .txt
  (`H|` = header rows) and check the columns line up with the years before translating it.

## Standard (agree it with the user; Apple FY2025, 2026-10-29)
About 20 notes per 100 sentences, covering four things:
1. **Line items and accounting** — what a line item is and how US GAAP recognises and measures it
   (revenue recognition for bundled services, deferred revenue, share-based compensation,
   uncertain tax positions …). Name the standard (ASC 606, ASC 740) only when sure.
2. **Where the numbers come from and how they tie** — show the arithmetic: year-over-year change
   and percentage, margin = gross margin ÷ net sales, effective tax rate = provision ÷ pre-tax
   income; how a figure in MD&A ties to the statements in Item 8, how the three statements connect.
3. **Business analysis** — what drives growth or decline by segment, region and product, why margins
   move (mix, costs, currency, tariffs), what the cash flows and capital return say.
4. **Risks and things to watch** — one-off items (a tax settlement, a legal charge), estimates and
   judgements, non-GAAP measures, management's hedged wording, concentration (China, a single
   product). Analysis only: never a recommendation to buy or sell.

## Writing
- Translate every sentence. Keep the filing's terms consistent; a starting glossary:
  net sales 净销售额 · cost of sales 销售成本 · gross margin 毛利 / 毛利率（percentage）·
  operating expenses 营业费用 · research and development 研发 · selling, general and administrative
  销售、一般及管理费用 · operating income 营业利润 · provision for income taxes 所得税费用 ·
  effective tax rate 实际税率 · net income 净利润 · earnings per share 每股收益（diluted 稀释）·
  marketable securities 有价证券 · term debt 定期债务 · commercial paper 商业票据 ·
  deferred revenue 递延收入 · share repurchase 股票回购 · fiscal year 财年.
- Numbers: quote them as the filing gives them ($416,161 million, $132.4 billion, 46.9%) so the
  reader can find them; add a conversion in brackets when it helps (约 4,162 亿美元).
  Work out every derived number in the note and show how ("(416,161 − 391,035) ÷ 391,035 ≈ 6.4%").
- `check.py` lists the numbers in notes that do not appear in the chapter. Each must be one you
  worked out (a difference, a ratio, a conversion); recompute it, and fix any that is a mistake.
- Apple's fiscal year ends on the last Saturday of September: "2025" means the year to September 27,
  2025. Say so where a reader could take it for the calendar year.
- Table cells: translate the row and column labels in `cells`; years, "Change" columns and numbers
  stay as they are.
- Cross-references: "Item 8 附注 7", "第 1A 项风险因素"; check the Note number against Item 8.

## Checks specific to filings
- Every number in a note matches the text or a table, or is shown as worked out.
- Every claim about a later or earlier year comes from this filing; say "本报告未披露" rather than
  bring in outside figures.
- No investment advice.
