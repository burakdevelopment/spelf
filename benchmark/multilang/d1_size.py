import pathlib
import re
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "compiler"))

import programs_a
import programs_b
import programs_c
from spelfc import lexer

P = {**programs_a.P, **programs_b.P, **programs_c.P}
TOK = re.compile(r"[A-Za-z_]\w*|\d+\.\d*(?:[eE][-+]?\d+)?[fF]?|0x[0-9a-fA-F]+|\d+[uUlL]*"
                 r"|->|::|<=|>=|==|!=|&&|\|\||\+\+|--|<<|>>|\.\.=|\.\.|=>|\S")

def toks_c(s: str) -> int:
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    s = re.sub(r"^\s*#include[^\n]*", "", s, flags=re.M)
    s = re.sub(r'"(?:\\.|[^"\\])*"', '"s"', s)
    return len(TOK.findall(s))

def toks_ada(files: dict) -> int:
    t = 0
    for n, s in files.items():
        if n.endswith(".adc"):
            continue
        s = re.sub(r"--[^\n]*", "", s)
        s = re.sub(r'"[^"]*"', '"s"', s)
        t += len(TOK.findall(s))
    return t

def toks_spelf(s: str) -> int:
    toks = [t for t in lexer.lex(s) if t.kind != "EOF"]
    if len(toks) >= 2 and toks[0].text == "module":
        i = 2
        while i + 1 < len(toks) and toks[i].text == ".":
            i += 2
        toks = toks[i:]
    return len(toks)

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    rows = []
    mdir = ROOT / "compiler" / "tests" / "mutants"
    for mid in sorted(P):
        f = next(mdir.glob(f"{mid}-*.fixed.spelf"))
        rows.append((mid, toks_spelf(f.read_text(encoding="utf-8")), toks_c(P[mid]["c"]["fixed"]),
                     toks_ada(P[mid]["ada"]["fixed"]), toks_c(P[mid]["rust"]["fixed"])))
    tot = [sum(r[i] for r in rows) for i in range(1, 5)]
    print(f"26 fixed programs, tokens: Spelf {tot[0]}  C {tot[1]}  Ada/SPARK {tot[2]}  Rust {tot[3]}")
    print(f"ratio / C: Spelf {tot[0] / tot[1]:.2f}  Ada/SPARK {tot[2] / tot[1]:.2f}  Rust {tot[3] / tot[1]:.2f}  "
          f"(median Spelf/C {statistics.median(r[1] / r[2] for r in rows):.2f})")
    ex = next((ROOT / "examples").glob("*-px4-rate-control"))
    ps = toks_spelf((ex / "rate_control.spelf").read_text(encoding="utf-8"))
    pc = toks_c((ex / "px4_style.c").read_text(encoding="utf-8"))
    print(f"PX4 rate controller: Spelf {ps}  C {pc}  ratio {ps / pc:.2f}")

if __name__ == "__main__":
    main()
