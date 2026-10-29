import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from spelfc import checker, interp, parser
from run_tests import header

def held_expiry_stub():
    st = {"calls": 0, "t": 0.0}

    def now_mono():
        st["t"] += 0.02
        return st["t"]

    def sensor():
        st["calls"] += 1
        return (0 if st["calls"] == 1 else 3, 1.0)
    return {"now_mono": now_mono, "sensor": sensor}

def dma_stub():
    from spelfc.interp import Res

    def bc_start(b):
        b.store["words"][1] = (b.store["words"][0] + 1) & 0xFFFF

    def bc_wait():
        return Res(None, "Fired")
    return {"bc_start": bc_start, "bc_wait": bc_wait}

STUBS = {"held-expiry.stub.c": held_expiry_stub, "T-typestate-dma.stub.c": dma_stub}

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    files = sorted(HERE.glob("mutants/*.spelf")) + sorted(HERE.glob("features/*.spelf")) + \
        sorted(HERE.glob("ok/*.spelf"))
    ran = ok = 0
    for f in files:
        src = f.read_text(encoding="utf-8")
        expect, out, stub = header(f)
        if expect != "OK" or not out:
            continue
        ran += 1
        prog = parser.parse(src, f.name)
        ck = checker.check(prog, f.name)
        ext = STUBS[stub]() if stub else {}
        try:
            got, _ = interp.run(ck, ext)
        except interp.Violation as v:
            print(f"VIOLATION  {f.name}: {v}")
            continue
        if got != out:
            print(f"DIFF   {f.name}: interpreter {got} ≠ C {out}")
            continue
        ok += 1
        print(f"SAME   {f.name:44s} {len(out)} lines")
    print(f"\n{ok}/{ran} programs: interpreter and generated C give the same output; no oracle violation")
    sys.exit(0 if ok == ran else 1)

if __name__ == "__main__":
    main()
