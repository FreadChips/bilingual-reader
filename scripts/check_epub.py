"""Structural check of an EPUB (a stand-in when epubcheck/Java is not available).

Usage: python check_epub.py BOOK.epub

Checks that mimetype comes first and uncompressed, every XHTML/OPF/NCX file is well-formed XML,
ids are unique per file, the manifest and the zip agree, and every src/href (including #anchors)
resolves. Prints the problems, or "problems 0".
"""
import collections
import posixpath
import sys
import zipfile
import xml.etree.ElementTree as ET


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    z = zipfile.ZipFile(sys.argv[1])
    names = z.namelist()
    probs = []
    first = z.infolist()[0]
    if first.filename != "mimetype" or first.compress_type != 0 or z.read("mimetype") != b"application/epub+zip":
        probs.append("mimetype entry wrong")
    ids, refs = {}, []
    for n in names:
        if n.endswith((".xhtml", ".opf", ".ncx", ".xml")):
            try:
                root = ET.fromstring(z.read(n))
            except ET.ParseError as e:
                probs.append(f"XML {n}: {e}")
                continue
            if n.endswith(".xhtml"):
                seen = collections.Counter(el.get("id") for el in root.iter() if el.get("id"))
                probs += [f"dup id {n}#{i}" for i, c in seen.items() if c > 1]
                ids[n] = set(seen)
                for el in root.iter():
                    for attr in ("src", "href"):
                        v = el.get(attr)
                        if v and not v.startswith(("http", "mailto")):
                            refs.append((n, v))
    opf_name = next(n for n in names if n.endswith(".opf"))
    opf = ET.fromstring(z.read(opf_name))
    O = "{http://www.idpf.org/2007/opf}"
    base = posixpath.dirname(opf_name)
    man = {posixpath.join(base, i.get("href")) for i in opf.iter(O + "item")}
    probs += [f"manifest missing file {m}" for m in man if m not in names]
    probs += [f"file not in manifest {n}" for n in names
              if n.startswith(base + "/") and n not in man and n != opf_name]
    for n, v in refs:
        f, _, frag = v.partition("#")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(n), f)) if f else n
        if target not in names:
            probs.append(f"broken {n} -> {v}")
        elif frag and target in ids and frag not in ids[target]:
            probs.append(f"broken anchor {n} -> {v}")
    print("files", len(names), "| xhtml", len(ids), "| refs", len(refs), "| problems", len(probs))
    for p in probs[:30]:
        print(" ", p)
    sys.exit(1 if probs else 0)


if __name__ == "__main__":
    main()
