import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "compiler"))
from spelfc import checker, interp, parser
from spelfc.diagnostics import SpelfError

def verdict(src, name):
    try:
        return checker.check(parser.parse(src, name), name), None
    except SpelfError as e:
        return None, e

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    mutations = json.loads((HERE / "mutations.json").read_text(encoding="utf-8"))
    failures = 0
    for f in sorted(HERE.glob("*/*.spelf")):
        src = f.read_text(encoding="utf-8")
        rel = f.relative_to(HERE).as_posix()
        ck, err = verdict(src, f.name)
        if err:
            print(f"FAIL {rel}: rejected [{err.rule}] {err.message}")
            failures += 1
            continue
        print(f"ok   {rel}: {sum(ck.stats.values())} obligations proved")
        if "main" in ck.fns:
            interp.Interp(ck).call("main", [])
        for m in (m for m in mutations if m["file"] == rel):
            if src.count(m["change"]) != 1:
                print(f"FAIL {rel}: text to change not found exactly once: {m['change'][:50]!r}")
                failures += 1
                continue
            _, e = verdict(src.replace(m["change"], m["to"]), f.name)
            got = e.rule if e else "accepted"
            failures += got != m["rejected_by"]
            print(f"{'ok  ' if got == m['rejected_by'] else 'FAIL'}   change {m['change'].splitlines()[0][:44]!r:48} "
                  f"-> expected {m['rejected_by']}, got {got}")
    known = {f.relative_to(HERE).as_posix() for f in HERE.glob("*/*.spelf")}
    for m in mutations:
        if m["file"] not in known:
            print(f"FAIL mutations.json names a missing file: {m['file']}")
            failures += 1
    print(f"{len(mutations)} documented rejections checked; "
          + ("all examples behave as documented" if not failures else f"{failures} failure(s)"))
    sys.exit(1 if failures else 0)

if __name__ == "__main__":
    main()
