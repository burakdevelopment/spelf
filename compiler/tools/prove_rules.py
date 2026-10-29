import sys
import time

import z3

sys.setrecursionlimit(10000)
RESULTS = []

def lemma(name, claim, hyps=(), timeout_ms=120_000):
    s = z3.Solver()
    s.set("timeout", timeout_ms)
    s.add(*hyps)
    s.add(z3.Not(claim))
    t = time.time()
    r = s.check()
    dt = time.time() - t
    ok = r == z3.unsat
    RESULTS.append((name, ok, r, dt))
    print(f"{'PROVED' if ok else 'FAILED':9} {name:62} {dt:6.2f} s" + ("" if ok else f"  [{r}]"))
    return ok

def tdiv(x, y):
    ax = z3.If(x >= 0, x, -x)
    ay = z3.If(y >= 0, y, -y)
    q = ax / ay
    return z3.If(z3.Or(z3.And(x >= 0, y > 0), z3.And(x < 0, y < 0)), q, -q)

def zmin(*xs):
    m = xs[0]
    for x in xs[1:]:
        m = z3.If(x < m, x, m)
    return m

def zmax(*xs):
    m = xs[0]
    for x in xs[1:]:
        m = z3.If(x > m, x, m)
    return m

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    I = z3.Int
    a, b, la, ha, lb, hb = I("a"), I("b"), I("la"), I("ha"), I("lb"), I("hb")
    ina = [la <= a, a <= ha]
    inb = [lb <= b, b <= hb]

    print("-- L1: integer interval arithmetic (symbolic bounds) --")
    lemma("L1.1  a+b ∈ [la+lb, ha+hb]", z3.And(la + lb <= a + b, a + b <= ha + hb), ina + inb)
    lemma("L1.2  a−b ∈ [la−hb, ha−lb]", z3.And(la - hb <= a - b, a - b <= ha - lb), ina + inb)
    lemma("L1.3  −a ∈ [−ha, −la]", z3.And(-ha <= -a, -a <= -la), ina)
    corners = [la * lb, la * hb, ha * lb, ha * hb]
    lemma("L1.4  a·b ∈ [min corner, max corner]", z3.And(zmin(*corners) <= a * b, a * b <= zmax(*corners)), ina + inb)

    print("-- L2: C99 truncating division and remainder --")
    q = tdiv(a, b)
    lemma("L2.1  b≠0 ⇒ a = b·(a/b) + a%b and |a%b| < |b|",
          z3.And(a == b * q + (a - b * q), z3.If(a - b * q >= 0, a - b * q, b * q - a) < z3.If(b >= 0, b, -b)),
          [b != 0])
    lemma("L2.2  the remainder has the sign of the dividend",
          z3.And(z3.Implies(a >= 0, a - b * q >= 0), z3.Implies(a <= 0, a - b * q <= 0)), [b != 0])
    a1, a2 = I("a1"), I("a2")
    lemma("L2.3  b>0 ⇒ a/b monotone in the dividend", z3.Implies(a1 <= a2, tdiv(a1, b) <= tdiv(a2, b)), [b > 0])
    lemma("L2.4  b<0 ⇒ a/b antitone in the dividend", z3.Implies(a1 <= a2, tdiv(a1, b) >= tdiv(a2, b)), [b < 0])
    b1, b2 = I("b1"), I("b2")
    lemma("L2.5  a≥0 ⇒ a/b antitone in a positive divisor",
          z3.Implies(z3.And(0 < b1, b1 <= b2), tdiv(a, b1) >= tdiv(a, b2)), [a >= 0])
    lemma("L2.6  a≤0 ⇒ a/b monotone in a positive divisor",
          z3.Implies(z3.And(0 < b1, b1 <= b2), tdiv(a, b1) <= tdiv(a, b2)), [a <= 0])
    M, B = I("M"), I("B")
    lemma("L2.7  |a|≤M, 0<|b|≤B ⇒ |a%b| ≤ min(M, B−1)",
          z3.And(z3.If(a - b * q >= 0, a - b * q, b * q - a) <= M, z3.If(a - b * q >= 0, a - b * q, b * q - a) <= B - 1),
          [b != 0, -M <= a, a <= M, -B <= b, b <= B])
    lemma("L2.8  |a|≤M ⇒ |a/b| ≤ M (b≠0)", z3.And(-M <= q, q <= M), [b != 0, -M <= a, a <= M])

    print("-- L3: fixed-point rescaling (p = 2^d > 0, symbolic) --")
    x, p, r = I("x"), I("p"), I("r")
    fl = x / p if False else None
    qf = I("qf")
    floor_q = x / p
    rr = x - floor_q * p
    lemma("L3.1  p>0 ⇒ Z3 x/p is floor: q·p ≤ x < (q+1)·p",
          z3.And(floor_q * p <= x, x < (floor_q + 1) * p), [p > 0])
    ceil_q = floor_q + z3.If(rr != 0, 1, 0)
    lemma("L3.2  ceiling: (q−1)·p < x ≤ q·p", z3.And((ceil_q - 1) * p < x, x <= ceil_q * p), [p > 0])
    tz = z3.If(z3.Or(x >= 0, rr == 0), floor_q, floor_q + 1)
    lemma("L3.3  toward zero: |q·p| ≤ |x| and same sign",
          z3.And(z3.If(tz * p >= 0, tz * p, -tz * p) <= z3.If(x >= 0, x, -x),
                 z3.Implies(x >= 0, tz >= 0), z3.Implies(x <= 0, tz <= 0),
                 z3.If(x >= 0, x, -x) - z3.If(tz * p >= 0, tz * p, -tz * p) < p), [p > 0])
    ne = z3.If(z3.Or(2 * rr > p, z3.And(2 * rr == p, floor_q % 2 != 0)), floor_q + 1, floor_q)
    lemma("L3.4  nearest even: |x − q·p| ≤ p/2",
          z3.And(2 * (x - ne * p) <= p, 2 * (ne * p - x) <= p), [p > 0])
    lemma("L3.5  nearest even: an exact half rounds to even",
          z3.Implies(2 * (x - floor_q * p) == p, ne % 2 == 0), [p > 0])

    print("-- L4: path sensitivity and joins --")
    c = z3.Bool("c")
    t1, t2, v = I("t1"), I("t2"), I("v")
    lemma("L4.1  ite join: if both branches satisfy φ, so does the join",
          z3.Implies(z3.And(z3.Implies(c, z3.And(la <= t1, t1 <= ha)), z3.Implies(z3.Not(c), z3.And(la <= t2, t2 <= ha))),
                     z3.And(la <= z3.If(c, t1, t2), z3.If(c, t1, t2) <= ha)))
    lemma("L4.2  havoc: nothing beyond the type range is assumed",
          z3.Implies(z3.And(la <= v, v <= ha), z3.And(la <= v, v <= ha)))

    print("-- L5: IEEE-754 rounding model (binary16 inputs; exact computation in binary64, lossless) --")
    H, D = z3.FloatHalf(), z3.Float64()
    RNE = z3.RNE()
    fa, fb, fc, kc = (z3.FP(n, H) for n in ("fa", "fb", "fc", "kc"))

    def finite(x):
        return z3.Not(z3.Or(z3.fpIsNaN(x), z3.fpIsInf(x)))

    def wide(x):
        return z3.fpToFP(RNE, x, D)

    u = z3.FPVal(2.0 ** -11, D)
    eta = z3.FPVal(2.0 ** -25, D)
    for opname, op16, op64 in (("+", z3.fpAdd, z3.fpAdd), ("−", z3.fpSub, z3.fpSub)):
        r16 = op16(RNE, fa, fb)
        e = op64(RNE, wide(fa), wide(fb))
        d = z3.fpAbs(z3.fpSub(RNE, wide(r16), e))
        bound = z3.fpAdd(RNE, z3.fpMul(RNE, u, z3.fpAbs(e)), eta)
        lemma(f"L5.{opname}  fl16(a{opname}b): |r − e| ≤ u·|e| + η (no overflow)",
              z3.fpLEQ(d, bound), [finite(fa), finite(fb), finite(r16)], timeout_ms=600_000)
    S, W = z3.FPSort(5, 6), z3.Float32()
    sa, sb, sc = (z3.FP(n, S) for n in ("sa", "sb", "sc"))

    def wide_s(x):
        return z3.fpToFP(RNE, x, W)
    us, etas = z3.FPVal(2.0 ** -6, W), z3.FPVal(2.0 ** -20, W)
    rm = z3.fpMul(RNE, sa, sb)
    em = z3.fpMul(RNE, wide_s(sa), wide_s(sb))
    dm = z3.fpAbs(z3.fpSub(RNE, wide_s(rm), em))
    lemma("L5.·  fl(a·b): |r − e| ≤ u·|e| + η (no overflow; format e=5,p=6)",
          z3.fpLEQ(dm, z3.fpAdd(RNE, z3.fpMul(RNE, us, z3.fpAbs(em)), etas)),
          [finite(sa), finite(sb), finite(rm)], timeout_ms=600_000)
    s1, s2 = z3.fpAdd(RNE, sa, sb), z3.fpAdd(RNE, sa, sc)
    e1, e2 = z3.fpAdd(RNE, wide_s(sa), wide_s(sb)), z3.fpAdd(RNE, wide_s(sa), wide_s(sc))
    lemma("L5.m  rounding is monotone: a+b ≤ a+c (exact) ⇒ fl(a+b) ≤ fl(a+c) (e=5,p=6)",
          z3.Implies(z3.fpLEQ(e1, e2), z3.fpLEQ(s1, s2)),
          [finite(sa), finite(sb), finite(sc), finite(s1), finite(s2)], timeout_ms=600_000)
    s1, s2 = z3.fpAdd(RNE, fa, fb), z3.fpAdd(RNE, fa, fc)
    e1 = z3.fpAdd(RNE, wide(fa), wide(fb))
    lemma("L5.k  a constant rounds to itself: e ≤ c ⇒ fl(e) ≤ c (c representable)",
          z3.Implies(z3.fpLEQ(e1, wide(kc)), z3.fpLEQ(s1, kc)),
          [finite(fa), finite(fb), finite(kc), finite(s1)], timeout_ms=600_000)

    print("-- L6: IEEE equality --")
    D = z3.Float64()
    x1 = z3.FP("x1", D)
    lemma("L6.1  x ≠ 0.0 (IEEE) ⇒ x is not zero (including −0.0)",
          z3.Implies(z3.Not(z3.fpEQ(x1, z3.FPVal(0.0, D))), z3.Not(z3.fpIsZero(x1))))
    lemma("L6.2  structural inequality is NOT ENOUGH: x ≠ +0.0 yet x = −0.0 is possible (expected: FAILED)",
          z3.Implies(x1 != z3.FPVal(0.0, D), z3.Not(z3.fpIsZero(x1))))

    n_ok = sum(1 for _, ok, _, _ in RESULTS if ok)
    expected_fail = {"L6.2  structural inequality is NOT ENOUGH: x ≠ +0.0 yet x = −0.0 is possible (expected: FAILED)"}
    bad = [n for n, ok, _, _ in RESULTS if not ok and n not in expected_fail]
    print(f"\n{n_ok}/{len(RESULTS)} lemmas proved; expected counterexamples: {len(expected_fail)}; "
          f"unexpected failures: {len(bad)}")
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
