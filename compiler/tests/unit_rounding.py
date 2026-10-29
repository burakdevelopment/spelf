import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import z3
from spelfc.checker import _round_py_int, _round_z3
from spelfc.codegen import RQ_HELPERS

MODES = ("nearest_even", "toward_zero", "floor", "ceil")
XS = range(-70, 71)
DS = (1, 2, 3, 4)

def main():
    bad = 0
    for m in MODES:
        for d in DS:
            for x in XS:
                zv = z3.simplify(_round_z3(z3.IntVal(x), 2 ** d, m)).as_long()
                if zv != _round_py_int(x, 2 ** d, m):
                    bad += 1
                    print("Z3 != Python", m, x, d, zv)
    lines = ["#include <stdint.h>", "#include <stdio.h>"] + list(RQ_HELPERS.values()) + ["int main(void) {"]
    for m in MODES:
        for d in DS:
            lines.append(f"    for (int64_t x = -70; x <= 70; ++x) {{ printf(\"%lld\\n\", (long long)spelf_rq_{m}(x, {d})); }}")
    lines += ["    return 0;", "}"]
    with tempfile.TemporaryDirectory() as t:
        c = pathlib.Path(t) / "r.c"
        c.write_text("\n".join(lines) + "\n")
        exe = pathlib.Path(t) / "r.exe"
        subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Werror", str(c), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], capture_output=True, text=True).stdout.split()
    i = 0
    for m in MODES:
        for d in DS:
            for x in XS:
                if int(out[i]) != _round_py_int(x, 2 ** d, m):
                    bad += 1
                    print("C != Python", m, x, d, out[i])
                i += 1
    n = len(MODES) * len(DS) * len(XS)
    print(f"{n} cases × 3 implementations (Z3, Python, C): {'all identical' if bad == 0 else f'{bad} MISMATCHES'}")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
