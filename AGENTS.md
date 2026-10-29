# Working on the bilingual-reader toolkit

This repository is an Agent Skill. To make a bilingual study edition, follow SKILL.md; you do not
need to change anything here.

When you change the toolkit itself (scripts/, templates/, references/):
- Run `python -I scripts/regress_all.py save` before the change and `compare` after it, on the books
  listed in testbed.yaml (copy testbed.example.yaml). Only intended chapters may change; a crash or a
  chapter that was not rewritten fails the run.
- Keep scripts free of book-specific values: those belong in each book's book.yaml, labels and patches.
- Record what you learned in references/lessons.md and the behaviour in references/auto-layout.md
  (PDF engine) or references/profile.md (book.yaml).
- Write Python 3.10-compatible code (no nested same-quote f-strings).
