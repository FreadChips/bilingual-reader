"""Remove single-asterisk italics (*x*) from annotation JSON files; keep **bold** and stars used as
symbols (k*, c*gold, f(k*)): the marked text must contain Chinese and the opening star must not
follow an ASCII letter, digit or bracket. --dry lists the matches without writing."""
import glob
import json
import re
import sys

PAT = re.compile(r'(?<![A-Za-z0-9)\]*])\*(?![*\s=])([^*\n"]*?[一-鿿][^*\n"]*?)(?<![\s*])\*(?![A-Za-z0-9*])')
dry = "--dry" in sys.argv
sys.stdout.reconfigure(encoding="utf-8")
n = 0
for f in glob.glob(sys.argv[1] + "/*.json"):
    s = open(f, encoding="utf-8").read()
    if dry:
        for m in PAT.finditer(s):
            print(f[-12:], repr(m.group(0)[:50]))
        continue
    t, k = PAT.subn(r"\1", s)
    json.loads(t)
    n += k
    open(f, "w", encoding="utf-8").write(t)
print("italics removed:", n)
