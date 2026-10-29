"""Regression check over every book in testbed.yaml: re-extract all chapters, compare with a snapshot.

Usage: python regress_all.py save       re-extract every book with the current toolkit, then snapshot it
       python regress_all.py compare    re-extract every book and compare it with the snapshot
       options: --testbed FILE          default: $BILINGUAL_TESTBED, else testbed.yaml in this skill
                --only macro,ax         a subset of the books, by name

Run `save` before changing the toolkit and `compare` after it. `save` re-extracts first, so the
snapshot always comes from the current code, never from a stale build/source.
Exit status: 0 no changes; 1 a chapter failed or was not rewritten (a crash hides the chapters after
it, so "no changes" would mean "never ran"); 2 chapters changed: read the log, every changed sentence
is listed there. Each run's full output goes to logs/ in the skill folder.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def run(args, env):
    p = subprocess.run([sys.executable, "-I", *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    testbed = os.environ.get("BILINGUAL_TESTBED") or HERE.parent / "testbed.yaml"
    only = None
    if "--testbed" in args:
        i = args.index("--testbed")
        testbed = args[i + 1]
        del args[i:i + 2]
    if "--only" in args:
        i = args.index("--only")
        only = set(args[i + 1].split(","))
        del args[i:i + 2]
    if args not in (["save"], ["compare"]):
        sys.exit(__doc__)
    mode = args[0]
    testbed = Path(testbed)
    if not testbed.exists():
        sys.exit(f"no {testbed}: copy testbed.example.yaml to testbed.yaml and list your own books there")
    books = yaml.safe_load(testbed.read_text(encoding="utf-8"))["books"]
    if only:
        books = [b for b in books if b["name"] in only]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    log_dir = HERE.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{mode}.log"
    log = [f"regress_all {mode} {time.strftime('%Y-%m-%d %H:%M')} testbed {testbed}"]
    rows, failed, changed = [], False, False

    for b in books:
        root = Path(b["dir"]) if Path(b["dir"]).is_absolute() else testbed.parent / b["dir"]
        print(f"{b['name']}: extracting {root} …", flush=True)
        start = time.time()
        rc, out = run([HERE / "extract.py", "--book", root, "all"], env)
        secs = time.time() - start
        log += [f"\n===== {b['name']} ({root}) extract all: exit {rc}, {secs:.0f} s", out]
        files = sorted((root / "build" / "source").glob("ch*.json"))
        stale = [f.stem for f in files if f.stat().st_mtime < start - 1]
        crashes = out.count("Traceback") + (rc != 0 and "Traceback" not in out)
        rc2, out2 = run([HERE / "regress.py", "--book", root, mode], env)
        log += [f"----- regress.py {mode}: exit {rc2}", out2]
        diff = [line[3:].strip() for line in out2.splitlines() if line.startswith("== ")]
        diff += [line.split(":")[0] for line in out2.splitlines() if "missing from build/source" in line]
        failed |= bool(crashes or stale or rc2)
        changed |= bool(diff)
        rows.append((b["name"], len(files), secs, crashes, stale, diff if mode == "compare" else None))

    print(f"\n{'book':8} {'chapters':>8} {'secs':>5}  {'crashes':>7}  not rewritten / changed")
    for name, n, secs, crashes, stale, diff in rows:
        note = []
        if stale:
            note.append("NOT REWRITTEN " + " ".join(stale))
        if diff:
            note.append("CHANGED " + " ".join(diff))
        elif diff is not None:
            note.append("no changes")
        else:
            note.append("snapshot saved")
        print(f"{name:8} {n:8} {secs:5.0f}  {crashes:7}  {'; '.join(note)}")
    log_path.write_text("\n".join(log), encoding="utf-8")
    print(f"\nfull output: {log_path}")
    if failed:
        print("FAILED: a chapter crashed or was not rewritten; read the log before trusting this run")
        sys.exit(1)
    sys.exit(2 if changed else 0)


if __name__ == "__main__":
    main()
