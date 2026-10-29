# bilingual-reader

把英文书（PDF 或 EPUB）做成逐句对照的双语精读本：英文原文逐句配中文译文，重要句子配学科注释，图表放在原处。产出一个浏览器阅读器和一个手机用的 EPUB。

这个文件夹是一个 Agent Skill（开放的 SKILL.md 格式），Claude Code 和 Codex 都能用。工作流程在 `SKILL.md`，细节在 `references/`，脚本在 `scripts/`，模板在 `templates/`。

## 安装到 agent

技能本体只放在这个仓库里。两个 agent 的技能目录各放一个指向这里的目录联接（junction），这样两边始终用同一份。在 PowerShell 里运行：

```powershell
New-Item -ItemType Junction -Path "$HOME\.claude\skills\bilingual-reader" -Target "D:\Projects\bilingual-reader"
New-Item -ItemType Junction -Path "$HOME\.codex\skills\bilingual-reader" -Target "D:\Projects\bilingual-reader"
```

其他支持 SKILL.md 的 agent 也一样：把这个文件夹链接到它的技能目录下。

## Python 环境

每本书的目录里建一个虚拟环境（大约 100 MB）：

```powershell
python -m venv .venv
.venv\Scripts\pip install -r D:\Projects\bilingual-reader\requirements.txt
```

## 怎么交给任意一个 agent 继续做

- 规矩和流程在 `SKILL.md`。
- 每本书的注释标准写在书目录下 `book.yaml` 的 `annotation:` 段：注释重点、每 100 句多少条注释、语言注释的门槛。
- 每本书的进度、章节大纲和交接说明在书目录的 `NOTES.md`。
- agent 先读这两个文件再动手，做完一章就更新 `NOTES.md`。不依赖任何 agent 自己的记忆。

## 每次开工

在书目录里运行 `status.py`：每章的状态和下一步一目了然。每章交付前 `build.py N` 和 `check.py N` 都必须通过。

## 修改工具

修改 `scripts/` 之前运行 `regress_all.py save`，改完运行 `regress_all.py compare`，结果干净再在这个仓库里提交。测试书清单在 `testbed.yaml`：两本已注释的书，加上 `D:\Projects\bilingual-testbed` 里的三本测试书（曼昆微观 PDF 走自动引擎、OpenStax、arXiv ResNet）。运行日志在 `logs/`，不进 git。

## 版权

书籍通常有版权。生成的阅读器、EPUB 和提取出的原文都只留在本地，不要公开发布。

## 已知问题（2026-10-29）

- auto 测试书和 10 月 27 日快照的差异已审查，审查报告是 testbed 里的 `auto/drift-review-2026-10-29.md`。5 处退步已在 2026-10-29 修复（第 21 章图 3、图题截断、第 2 章 Table 1、小节标题、CASE STUDY），宏观已注释章节不受影响。还剩一个以前就有的小问题：当 “STUDY” 那一行被版面模型框进段落里时，会混进句子中间。
- 论文（arXiv ResNet）：行首的 “Fig. 6 (middle) shows …” 被当成图注。图题截断已修复。
- PDF 表格已能读成单元格（`auto.table_grid`，新书默认打开）；读不准的表仍是图片。EPUB 来源的表格还是每行一个字符串。
- 不支持扫描版 PDF（需要 OCR），也不支持 HTML 输入。EPUB 输入只在一本合成书上测试过，而且不在回归测试书里。
