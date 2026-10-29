import argparse
import pathlib
import random
import sys
import tempfile
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from spelfc import checker, codegen, interp, parser
from spelfc.diagnostics import SpelfError

RANGES = [(0, 10), (-10, 10), (0, 255), (-128, 127), (-1000, 1000), (0, 65535), (1, 100),
          (-(2 ** 31), 2 ** 31 - 1), (0, 1), (-5, 5), (0, 4095)]

def _tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q

LIM = 2 ** 62

def _lit(v):
    return str(v) if v >= 0 else f"(0 - {-v})"

class Gen:

    def __init__(self, rng: random.Random):
        self.r = rng
        self.fns = []
        self.states = []
        self.n = 0

    def fresh(self, base):
        self.n += 1
        return f"{base}{self.n}"

    def rng_(self):
        return self.r.choice(RANGES)

    @staticmethod
    def ty(rg):
        return f"Int<{rg[0]}..={rg[1]}>"

    def lit(self, rg=None):
        lo, hi = rg or self.r.choice([(-20, 20), (0, 300), (-3, 3)])
        v = self.r.randint(lo, hi)
        return _lit(v), (v, v)

    def cover(self, iv):
        r = self.r
        if r.random() < 0.8:
            cands = [rg for rg in RANGES if rg[0] <= iv[0] and iv[1] <= rg[1]]
            if cands:
                return r.choice(cands)
            return (max(iv[0], -LIM), min(iv[1], LIM))
        width = iv[1] - iv[0]
        lo = iv[0] + (r.randint(0, width) if width < 10 ** 6 else width // 3)
        hi = max(lo, iv[1] - (width // 3))
        return (min(lo, hi), hi)

    def clamp(self, e, rg):
        t, iv = e
        if rg[0] <= iv[0] and iv[1] <= rg[1]:
            return e
        lo, hi = _lit(rg[0]), _lit(rg[1])
        if self.r.random() < 0.9:
            return f"(if {t} < {lo} {{ {lo} }} else {{ if {t} > {hi} {{ {hi} }} else {{ {t} }} }})", rg
        return f"narrow<{self.ty(rg)}>({t})", rg

    def expr(self, scope, depth):
        r = self.r
        if depth <= 0 or r.random() < 0.3:
            if scope and r.random() < 0.75:
                name, rg = r.choice(scope)
                return name, rg
            return self.lit()
        k = r.random()
        if k < 0.45:
            op = r.choice(["+", "-", "*", "+", "-", "/", "%"])
            (a, ia), (b, ib) = self.expr(scope, depth - 1), self.expr(scope, depth - 1)
            if op in ("/", "%"):
                if r.random() < 0.7 or ib[0] <= 0 <= ib[1]:
                    d = r.randint(1, 9)
                    b, ib = str(d), (d, d)
            if op == "+":
                iv = (ia[0] + ib[0], ia[1] + ib[1])
            elif op == "-":
                iv = (ia[0] - ib[1], ia[1] - ib[0])
            elif op == "*":
                c = [x * y for x in ia for y in ib]
                iv = (min(c), max(c))
            elif op == "/":
                c = [_tdiv(x, y) for x in ia for y in ib]
                iv = (min(c), max(c))
            else:
                M = max(abs(ia[0]), abs(ia[1]))
                B = max(abs(ib[0]), abs(ib[1]))
                iv = (0 if ia[0] >= 0 else -min(M, B - 1), 0 if ia[1] <= 0 else min(M, B - 1))
            if abs(iv[0]) > LIM or abs(iv[1]) > LIM:
                return a, ia
            return f"({a} {op} {b})", iv
        if k < 0.6:
            c = self.cond(scope, depth - 1)
            (a, ia), (b, ib) = self.expr(scope, depth - 1), self.expr(scope, depth - 1)
            return f"(if {c} {{ {a} }} else {{ {b} }})", (min(ia[0], ib[0]), max(ia[1], ib[1]))
        if k < 0.75 and self.fns:
            name, params, ret = r.choice(self.fns)
            args = ", ".join(self.clamp(self.expr(scope, depth - 1), prg)[0] for _, prg in params)
            return f"{name}({args})", ret
        if k < 0.85:
            e = self.expr(scope, depth - 1)
            rg = self.cover(e[1])
            return f"narrow<{self.ty(rg)}>({e[0]})", rg
        return self.expr(scope, depth - 1)

    def cond(self, scope, depth):
        r = self.r
        op = r.choice(["<", "<=", ">", ">=", "==", "!="])
        c = f"{self.expr(scope, depth)[0]} {op} {self.expr(scope, depth)[0]}"
        if r.random() < 0.2:
            c = f"({c}) {r.choice(['&&', '||'])} ({self.expr(scope, depth)[0]} {r.choice(['<', '>'])} {self.lit()[0]})"
        return c

    def stmts(self, scope, mutables, depth, ret_rg, budget):
        r = self.r
        out = []
        scope = list(scope)
        for _ in range(r.randint(1, budget)):
            k = r.random()
            if k < 0.3:
                name = self.fresh("x")
                e = self.expr(scope, 3)
                rg = self.cover(e[1])
                if r.random() < 0.5:
                    e = self.clamp(e, rg)
                kw = "var" if r.random() < 0.4 else "let"
                out.append(f"{kw} {name}: {self.ty(rg)} = {e[0]};")
                scope.append((name, rg))
                if kw == "var":
                    mutables.append((name, rg))
            elif k < 0.5 and (mutables or self.states):
                name, rg = r.choice(mutables + self.states)
                e = self.expr(scope, 3)
                if r.random() < 0.85:
                    e = self.clamp(e, rg)
                out.append(f"{name} = {e[0]};")
            elif k < 0.7 and depth > 0:
                c = self.cond(scope, 2)
                a = self.stmts(scope, list(mutables), depth - 1, ret_rg, 3)
                b = self.stmts(scope, list(mutables), depth - 1, ret_rg, 3)
                out.append(f"if {c} {{ {' '.join(a)} }} else {{ {' '.join(b)} }}")
            elif k < 0.8 and depth > 0:
                i = self.fresh("i")
                lo = r.randint(0, 3)
                hi = lo + r.randint(1, 5)
                body = self.stmts(scope + [(i, (lo, hi - 1))], list(mutables), depth - 1, ret_rg, 2)
                out.append(f"for {i} in {lo}..{hi} {{ {' '.join(body)} }}")
            elif k < 0.9 and ret_rg is not None:
                c = self.cond(scope, 2)
                e = self.expr(scope, 2)
                if r.random() < 0.85:
                    e = self.clamp(e, ret_rg)
                out.append(f"if {c} {{ return {e[0]}; }}")
        return out

    def program(self):
        r = self.r
        lines = ["module Fuzz.P"]
        for _ in range(r.randint(0, 2)):
            name = self.fresh("s")
            rg = self.rng_()
            lines.append(f"state {name}: {self.ty(rg)} = {max(rg[0], min(rg[1], 0))};")
            self.states.append((name, rg))
        for _ in range(r.randint(1, 4)):
            name = self.fresh("f")
            params = [(self.fresh("p"), self.rng_()) for _ in range(r.randint(0, 3))]
            scope = list(params) + ([(s, rg) for s, rg in self.states])
            tail = self.expr(scope, 3)
            ret = self.cover(tail[1])
            body = self.stmts(scope, [], 2, ret, 5)
            if r.random() < 0.85:
                tail = self.clamp(tail, ret)
            pstr = ", ".join(f"{p}: {self.ty(rg)}" for p, rg in params)
            ens = ""
            if r.random() < 0.3:
                ens = f" ensures result {r.choice(['>=', '<='])} {_lit(ret[0] if r.random() < 0.5 else ret[1])}"
            lines.append(f"fn {name}({pstr}) -> {self.ty(ret)}{ens} {{")
            lines += ["  " + s for s in body]
            lines.append(f"  {tail[0]}")
            lines.append("}")
            self.fns.append((name, params, ret))
        return "\n".join(lines) + "\n"

FRANGES = [(0.0, 1.0), (-1.0, 1.0), (-35.0, 35.0), (0.0, 0.3), (-0.1, 0.1), (-500.0, 500.0), (0.001, 0.02),
           (-3.0, 3.0), (0.0, 100.0)]
FLITS = [0.1, 0.2, 0.3, 0.5, 1.0, 2.5, 3.0, 0.001, 7.0, 0.7, 1e-3, 0.25, 12.5]

def _flit(v):
    return repr(float(v)) if v >= 0 else f"(-{repr(float(-v))})"

class FloatGen:

    def __init__(self, rng):
        self.r = rng
        self.fns = []
        self.states = []
        self.n = 0
        self.bits = 32 if rng.random() < 0.4 else 64

    def fresh(self, base):
        self.n += 1
        return f"{base}{self.n}"

    def ty(self, rg):
        return f"Float{'32' if self.bits == 32 else ''}<1, {repr(float(rg[0]))}..={repr(float(rg[1]))}>"

    def lit(self):
        v = self.r.choice(FLITS) * (-1 if self.r.random() < 0.3 else 1)
        return _flit(v), (v, v)

    def cover(self, iv):
        r = self.r
        lo, hi = iv
        if r.random() < 0.75:
            cands = [rg for rg in FRANGES if rg[0] <= lo and hi <= rg[1]]
            if cands:
                return r.choice(cands)
            pad = (hi - lo) * 0.01 + 1e-9
            return (lo - pad, hi + pad)
        w = hi - lo
        return (lo + w * r.random() * 0.3, hi - w * r.random() * 0.3)

    def clamp(self, e, rg):
        t, iv = e
        if rg[0] <= iv[0] and iv[1] <= rg[1]:
            return e
        lo, hi = _flit(rg[0]), _flit(rg[1])
        return f"(if {t} < {lo} {{ {lo} }} else {{ if {t} > {hi} {{ {hi} }} else {{ {t} }} }})", rg

    def expr(self, scope, depth):
        r = self.r
        if depth <= 0 or r.random() < 0.3:
            if scope and r.random() < 0.8:
                return r.choice(scope)
            return self.lit()
        k = r.random()
        if k < 0.55:
            op = r.choice(["+", "-", "*", "+", "-", "/"])
            (a, ia), (b, ib) = self.expr(scope, depth - 1), self.expr(scope, depth - 1)
            if op == "/":
                d = r.choice([2.0, 4.0, 0.5, 3.0, 10.0, 7.0])
                b, ib = repr(d), (d, d)
            if op == "+":
                iv = (ia[0] + ib[0], ia[1] + ib[1])
            elif op == "-":
                iv = (ia[0] - ib[1], ia[1] - ib[0])
            elif op == "*":
                c = [x * y for x in ia for y in ib]
                iv = (min(c), max(c))
            else:
                c = [x / y for x in ia for y in ib]
                iv = (min(c), max(c))
            if max(abs(iv[0]), abs(iv[1])) > 1e6:
                return a, ia
            return f"({a} {op} {b})", iv
        if k < 0.75:
            c = self.cond(scope, depth - 1)
            (a, ia), (b, ib) = self.expr(scope, depth - 1), self.expr(scope, depth - 1)
            return f"(if {c} {{ {a} }} else {{ {b} }})", (min(ia[0], ib[0]), max(ia[1], ib[1]))
        if self.fns and k < 0.9:
            name, params, ret = r.choice(self.fns)
            args = ", ".join(self.clamp(self.expr(scope, depth - 1), prg)[0] for _, prg in params)
            return f"{name}({args})", ret
        return self.expr(scope, depth - 1)

    def cond(self, scope, depth):
        op = self.r.choice(["<", "<=", ">", ">="])
        return f"{self.expr(scope, depth)[0]} {op} {self.expr(scope, depth)[0]}"

    def stmts(self, scope, mutables, depth, ret_rg, budget):
        r = self.r
        out = []
        scope = list(scope)
        for _ in range(r.randint(1, budget)):
            k = r.random()
            if k < 0.35:
                name = self.fresh("x")
                e = self.expr(scope, 3)
                rg = self.cover(e[1])
                e = self.clamp(e, rg)
                kw = "var" if r.random() < 0.4 else "let"
                out.append(f"{kw} {name}: {self.ty(rg)} = {e[0]};")
                scope.append((name, rg))
                if kw == "var":
                    mutables.append((name, rg))
            elif k < 0.55 and (mutables or self.states):
                name, rg = r.choice(mutables + self.states)
                e = self.clamp(self.expr(scope, 3), rg)
                out.append(f"{name} = {e[0]};")
            elif k < 0.75 and depth > 0:
                c = self.cond(scope, 2)
                a = self.stmts(scope, list(mutables), depth - 1, ret_rg, 3)
                b = self.stmts(scope, list(mutables), depth - 1, ret_rg, 3)
                out.append(f"if {c} {{ {' '.join(a)} }} else {{ {' '.join(b)} }}")
            elif k < 0.85 and ret_rg is not None:
                c = self.cond(scope, 2)
                e = self.clamp(self.expr(scope, 2), ret_rg)
                out.append(f"if {c} {{ return {e[0]}; }}")
        return out

    def program(self):
        r = self.r
        lines = ["module Fuzz.F"]
        for _ in range(r.randint(0, 2)):
            name = self.fresh("s")
            rg = r.choice(FRANGES)
            init = max(rg[0], min(rg[1], 0.0))
            lines.append(f"state {name}: {self.ty(rg)} = {_flit(init)};")
            self.states.append((name, rg))
        for _ in range(r.randint(1, 3)):
            name = self.fresh("f")
            params = [(self.fresh("p"), r.choice(FRANGES)) for _ in range(r.randint(1, 3))]
            scope = list(params) + list(self.states)
            tail = self.expr(scope, 3)
            ret = self.cover(tail[1])
            body = self.stmts(scope, [], 2, ret, 4)
            tail = self.clamp(tail, ret)
            pstr = ", ".join(f"{p}: {self.ty(rg)}" for p, rg in params)
            lines.append(f"fn {name}({pstr}) -> {self.ty(ret)} {{")
            lines += ["  " + s for s in body]
            lines.append(f"  {tail[0]}")
            lines.append("}")
            self.fns.append((name, params, ret))
        return "\n".join(lines) + "\n"

def drive(ck, fns, rng, calls):
    it = interp.Interp(ck)
    it.max_steps = 200_000
    for _ in range(calls):
        name, params, _ = rng.choice(fns)
        args = []
        for _, (lo, hi) in params:
            if isinstance(lo, float):
                import struct
                v = rng.choice([lo, hi, 0.0 if lo <= 0 <= hi else lo, rng.uniform(lo, hi), rng.uniform(lo, hi)])
                if getattr(ck, "_fuzz_bits", 64) == 32:
                    v = struct.unpack("f", struct.pack("f", v))[0]
                    lo32, hi32 = (struct.unpack("f", struct.pack("f", x))[0] for x in (lo, hi))
                    v = min(max(v, lo32), hi32)
                args.append(v)
            else:
                args.append(rng.choice([lo, hi, 0 if lo <= 0 <= hi else lo, rng.randint(lo, hi), rng.randint(lo, hi)]))
        try:
            it.call(name, args)
        except interp.PreconditionFalse:
            pass
    return it

def c_differential(ck, fns, rng, work):
    from spelfc.__main__ import run_c
    calls = []
    for _ in range(6):
        name, params, _ = rng.choice(fns)
        calls.append((name, [rng.randint(lo, hi) for _, (lo, hi) in params]))
    it = interp.Interp(ck)
    expect = []
    for name, args in calls:
        try:
            expect.append(str(it.call(name, args)))
        except interp.PreconditionFalse:
            return None
    csrc = codegen.generate(ck)
    if not getattr(ck, "dead_code_check", True):
        csrc = ('#pragma GCC diagnostic ignored "-Wtype-limits"\n'
                '#pragma GCC diagnostic ignored "-Wtautological-compare"\n') + csrc
    body = "\n".join(f'    printf("%" PRId64 "\\n", (int64_t)f_{n}({", ".join(str(a) + ("LL" if abs(a) > 2**31 - 1 else "") for a in args)}));'
                     for n, args in calls)
    csrc += "\nint main(void) {\n" + body + "\n    return 0;\n}\n"
    cp = run_c(csrc, None, work)
    got = cp.stdout.strip().splitlines()
    return None if got == expect else (expect, got, csrc)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--calls", type=int, default=60)
    ap.add_argument("--c", action="store_true", help="also cross-check accepted programs against C (slow)")
    ap.add_argument("--float", action="store_true", help="floating-point programs (soundness of the IEEE model)")
    ap.add_argument("--no-dead-code", action="store_true",
                    help="disable R-606: more programs are accepted, soundness is exercised more widely")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    fail_dir = HERE / "fuzz_failures"
    stats = {"generated": 0, "accepted": 0, "red": 0, "violation": 0, "crash": 0, "C-diff": 0, "C-checked": 0}
    rules = {}
    t0 = time.time()
    for i in range(a.n):
        rng = random.Random(a.seed * 1_000_003 + i)
        g = FloatGen(rng) if a.float else Gen(rng)
        src = g.program()
        stats["generated"] += 1
        try:
            prog = parser.parse(src, "fuzz.spelf")
            ck = checker.Checker(prog, "fuzz.spelf")
            ck.dead_code_check = not a.no_dead_code
            ck.check()
        except SpelfError as e:
            stats["red"] += 1
            rules[e.rule] = rules.get(e.rule, 0) + 1
            continue
        except Exception:
            stats["crash"] += 1
            fail_dir.mkdir(exist_ok=True)
            (fail_dir / f"crash-{a.seed}-{i}.spelf").write_text(src + "\n" + "".join("// " + l + "\n" for l in traceback.format_exc().splitlines()),
                                                               encoding="utf-8")
            continue
        stats["accepted"] += 1
        ck._fuzz_bits = getattr(g, "bits", 64)
        try:
            drive(ck, g.fns, rng, a.calls)
        except interp.Violation as v:
            stats["violation"] += 1
            fail_dir.mkdir(exist_ok=True)
            (fail_dir / f"unsound-{a.seed}-{i}.spelf").write_text(src + f"\n// {v}\n", encoding="utf-8")
            print(f"SOUNDNESS VIOLATION #{i}: {v}")
            continue
        except interp.HaltSignal:
            pass
        if a.c and not a.float:
            with tempfile.TemporaryDirectory() as d:
                try:
                    res = c_differential(ck, g.fns, rng, pathlib.Path(d))
                    stats["C-checked"] += 1
                except (interp.Violation, RuntimeError) as ex:
                    res = ("?", str(ex)[:300], "")
                if res is not None:
                    stats["C-diff"] += 1
                    fail_dir.mkdir(exist_ok=True)
                    (fail_dir / f"cdiff-{a.seed}-{i}.spelf").write_text(
                        src + f"\n// interpreter {res[0]} / C {res[1]}\n", encoding="utf-8")
                    print(f"C DIFFERENCE #{i}: interpreter {res[0]} / C {res[1]}")
    dt = time.time() - t0
    print(f"\n{stats}  ({dt:.0f} s)")
    top = sorted(rules.items(), key=lambda kv: -kv[1])[:8]
    print("rejection reasons:", ", ".join(f"{k}:{v}" for k, v in top))
    sys.exit(1 if stats["violation"] or stats["crash"] or stats["C-diff"] else 0)

if __name__ == "__main__":
    main()
