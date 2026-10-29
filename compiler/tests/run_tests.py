import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from spelfc import checker, codegen, parser
from spelfc.__main__ import run_c
from spelfc.diagnostics import SpelfError

_MANIFEST = None

def header(path: pathlib.Path):
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = json.loads((HERE / "expectations.json").read_text(encoding="utf-8"))
    e = _MANIFEST.get(f"{path.parent.name}/{path.name}", {})
    return e.get("expect"), list(e.get("out", [])), e.get("stub")

def run_one(path: pathlib.Path):
    src = path.read_text(encoding="utf-8")
    expect, out, stub = header(path)
    if expect is None:
        return False, "no expectation in tests/expectations.json"
    try:
        prog = parser.parse(src, path.name)
        ck = checker.check(prog, path.name)
        csrc = codegen.generate(ck)
    except SpelfError as e:
        if expect == "OK":
            return False, "unexpected error: " + e.render()
        if e.rule != expect:
            return False, f"wrong rule: expected {expect}, got {e.rule}\n      {e.render()}"
        return True, f"rejected [{e.rule}]  {e.message[:70]}"
    if expect != "OK":
        return False, f"NOT REJECTED, expected {expect}"
    if not out and "fn main" not in src:
        with tempfile.TemporaryDirectory() as d:
            try:
                wrapper = csrc + "\nint main(void) { return 0; }\n" if "int main(void)" not in csrc else csrc
                stubs = HERE / "stubs" / "all.c"
                run_c(wrapper, stubs if stubs.exists() else None, pathlib.Path(d))
            except RuntimeError as ex:
                return False, str(ex)[:400]
        return True, f"accepted ({sum(ck.stats.values())} obligations proved, C compiled)"
    with tempfile.TemporaryDirectory() as d:
        try:
            st = (path.parent / stub) if stub else None
            cp = run_c(csrc, st, pathlib.Path(d))
        except RuntimeError as ex:
            return False, str(ex)[:600]
    got = cp.stdout.strip().splitlines()
    if got != out:
        return False, f"output differs: expected {out}, got {got}"
    return True, f"accepted and ran ({sum(ck.stats.values())} obligations proved)"

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    files = [f for d in ("mutants", "features", "ok") for f in sorted((HERE / d).glob("*.spelf"))]
    passed = 0
    for f in files:
        ok, msg = run_one(f)
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {f.parent.name}/{f.name:<38} {msg}")
    print(f"\n{passed}/{len(files)} tests passed")
    return 0 if passed == len(files) else 1

if __name__ == "__main__":
    sys.exit(main())
