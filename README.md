# bilingual-reader · 双语精读本

把英文的**书籍、论文或财报**做成逐句对照的双语精读版：每句英文原文下面是中文译文，重要的句子附学科解读，个别难词难句附语言提示，图表放在正文提到的地方，表格变成可读的双语表格。产出两样东西：

- `reader/index.html`：浏览器阅读器，可以显示、遮挡或隐藏译文，开关各类注释，记住阅读位置，不需要联网；
- `export/<名字>.epub`：手机上用苹果图书、微信读书等阅读器离线阅读。

这是一个 [Agent Skill](https://agentskills.io)（SKILL.md 格式）。翻译和注释由你的 coding agent（Claude Code、Codex 等）一章一章完成，本仓库提供流程、提取引擎和检查工具：**原文永远由程序从文件里取，agent 不重打原文**；agent 负责判断结构、翻译和写注释；脚本负责检查对齐、格式、注释密度和数字。

## 三类素材

| 类型 | 输入 | 一章是什么 | 注释侧重（可以和 agent 另行约定） | 阅读器风格 |
| --- | --- | --- | --- | --- |
| 书籍 `book` | PDF（文字型）或 EPUB | 书的一章 | 概念、机制、常见误解、读图方法、章节联系 | 暖色纸张、衬线字体、卡片 |
| 论文 `paper` | PDF（如 arXiv） | 整篇论文 | 问题与贡献、方法原理、实验怎么读、背景与后续影响 | 学术排版、三线表、旁注 |
| 财报 `filing` | SEC 10-K 等 HTML 文件 | 一个 Item（如 MD&A、财务报表） | 科目与会计处理、数字勾稽、经营分析、风险信号 | 财经报告、报表式表格、批注 |

## 安装

需要：Python 3.10 以上、git，以及 Claude Code 或 Codex（或其他支持 SKILL.md 技能的 agent）。

```bash
git clone https://github.com/FreadChips/bilingual-reader.git
cd bilingual-reader
```

Windows（PowerShell）：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

macOS / Linux：

```bash
sh ./install.sh
```

安装脚本会：

1. 把这个文件夹链接到 `~/.claude/skills/bilingual-reader` 和 `~/.codex/skills/bilingual-reader`，两个 agent 用的是同一份；
2. 在这个文件夹里建 `.venv`，安装 PyMuPDF（含版面模型）和 PyYAML，约 100 MB。

以后更新只需要 `git pull`。只想链接、不装 Python 包：加 `-NoVenv`（Windows）或 `--no-venv`。

## 使用

1. 新建一个文件夹，把你的材料放进去（一本书一个文件夹）。
2. 在这个文件夹里打开 Claude Code 或 Codex，直接说，例如：
   - “把 mybook.pdf 做成双语精读本”
   - “把这篇论文做成双语精读版”
   - “把这份 10-K 的 Item 7 和 Item 8 做成双语精读版”
3. agent 会按 SKILL.md 的流程来：
   - 建立书目录和配置；
   - **先和你约定注释标准**（侧重什么、每 100 句大约多少条、语言提示的门槛）；
   - 提取原文，看版面图修正结构；
   - 逐章翻译和注释，跑检查；
   - 每做完一章交给你审，你批准后再做下一章。
4. 阅读：在书目录的 `reader` 文件夹里运行 `python -m http.server`，浏览器打开显示的地址；手机上用 `export` 里的 EPUB。

任何一个 agent 中途接手都没问题：进度、约定和交接说明都写在书目录的 `NOTES.md` 和 `book.yaml` 里，不依赖某个 agent 的记忆。开工时 agent 会先运行 `status.py` 看每章的状态和下一步。

## 常用命令

`K` 指本仓库的 `scripts` 文件夹，`python` 指 `.venv` 里的 Python，命令在书目录里运行：

| 命令 | 作用 |
| --- | --- |
| `python -I K/new_book.py DIR 材料.pdf [--kind paper\|filing] [--subject 学科]` | 建书目录 |
| `python -I K/status.py` | 每章的状态和下一步 |
| `python -I K/extract.py 3` | 提取第 3 章原文 |
| `python -I K/overlay.py 3 --review` | PDF：把需要复查的页面渲染成区域图 |
| `python -I K/build.py 3` | 合并译文注释，生成阅读器数据 |
| `python -I K/check.py 3` | 交付前检查（不需要浏览器） |
| `python -I K/quality.py` | 全书注释密度、术语一致性 |
| `python -I K/export_epub.py` | 导出 EPUB |

完整流程见 [SKILL.md](SKILL.md)，细节在 `references/`。

## 能做到什么、不能做什么

- **PDF**：自动版面引擎（本地运行的版面模型）找出标题、段落、图表、方框、页边术语；表格尽量读成单元格，读不准的保留为图片。已在两本曼昆经济学教材、OpenStax 经济学原理和一篇 arXiv 双栏论文上测试。
- **EPUB**：按 HTML 结构提取；目前只在一本合成的 EPUB 上测试过。
- **HTML 财报**：按 Item 分章，表格直接从 HTML 单元格读取，数字原样保留；已在 Apple 2025 财年 10-K 上测试。
- **不支持扫描版 PDF**（需要先做 OCR）。
- 翻译和注释的质量取决于你使用的模型；脚本只能检查格式、对齐、密度和数字，不能替你判断解读是否正确。每章交付前请自己读一遍。

## 版权

书籍和大部分论文受版权保护。本仓库只包含工具，不包含任何原文或成品。你用它为自己的材料制作的阅读器和 EPUB，请只用于个人学习，不要公开发布。

## 参与开发

改动提取引擎前后请跑回归检查：把 `testbed.example.yaml` 复制成 `testbed.yaml`，列出你本机的书目录，然后运行 `scripts/regress_all.py save`、改动、再运行 `compare`。详见 [AGENTS.md](AGENTS.md) 和 `references/lessons.md`。

## 已知问题

- 论文和小文档里，跨行断开的复合词（如 high-level）可能被连成一个词，需要用补丁修正。
- 当“CASE / STUDY”这类竖排标签被版面模型框进段落时，会混进句子中间。
- EPUB 来源的表格仍是每行一个字符串，还没有变成单元格。
- 手机阅读器对宽表格的支持各不相同，宽表在 EPUB 里可能需要缩放查看。

## 许可证

[AGPL-3.0](LICENSE)。本工具依赖的 PyMuPDF 同样采用 AGPL-3.0（或 Artifex 商业许可）。

---

## English summary

**bilingual-reader** turns an English book, research paper or financial report (text PDF, EPUB, or an SEC filing saved as HTML) into a sentence-by-sentence English–Chinese study edition: every sentence with its translation, subject notes on the important ones, figures where the text cites them and tables as bilingual grids. It produces an offline browser reader and an EPUB.

It is an Agent Skill for Claude Code, Codex and other agents that read SKILL.md. The program extracts the text from the source, so the agent never retypes the original. The agent decides the structure, translates and annotates chapter by chapter, and asks for approval after each one. Scripts check alignment, markup, note density and the numbers quoted in notes.

Install: clone the repository, then run `install.ps1` (Windows) or `install.sh` (macOS/Linux). The script links the skill into `~/.claude/skills` and `~/.codex/skills` and sets up a Python virtual environment. To use it, open a folder holding your material in your agent and ask for a bilingual study edition. Licence: AGPL-3.0.
