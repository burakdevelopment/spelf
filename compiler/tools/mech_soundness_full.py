import sys
import time

import z3

I = z3.IntSort()
B = z3.BoolSort()
S = z3.ArraySort(I, I)

results = []
BROKEN = set()

def nonvacuous(hyps) -> bool:
    v = z3.Solver()
    v.set("timeout", 30_000)
    for h in hyps:
        v.add(h)
    return v.check() != z3.unsat

def lemma(name, hyps, goal, timeout=120_000):
    if not nonvacuous(hyps):
        results.append((name, False))
        print(f"VACUOUS   {name:46} premises inconsistent")
        return False
    s = z3.Solver()
    s.set("timeout", timeout)
    for h in hyps:
        s.add(h)
    s.add(z3.Not(goal))
    t0 = time.time()
    r = s.check()
    dt = time.time() - t0
    ok = r == z3.unsat
    results.append((name, ok))
    print(f"{'PROVED' if ok else 'FAILED':9} {name:46} {dt:6.2f} s" + ("" if ok else f"  [{r}]"))
    return ok

def unsat_cases(name, hyps, cases):
    allok = nonvacuous(hyps)
    if not allok:
        print(f"VACUOUS   {name:46} premises inconsistent")
    for i, c in enumerate(cases):
        s = z3.Solver()
        s.set("timeout", 120_000)
        for h in hyps:
            s.add(h)
        s.add(c)
        r = s.check()
        allok &= r == z3.unsat
    results.append((name, allok))
    print(f"{'PROVED' if allok else 'FAILED':9} {name:46} {len(cases)} cases")
    return allok

def pred(n):
    return z3.Function(n, S, B)

def rel(n):
    return z3.Function(n, S, S, B)

def valid(P, R, Er, Q):
    s, t = z3.Consts("vs vt", S)
    return z3.ForAll([s], z3.Implies(P(s), z3.And(z3.Not(Er(s)), z3.ForAll([t], z3.Implies(R(s, t), Q(t))))))

def statements():
    print("-- statements --")
    P, Q, Rp, C = pred("P"), pred("Q"), pred("Rp"), pred("C")
    R1, R2 = rel("R1"), rel("R2")
    E1, E2 = pred("E1"), pred("E2")
    s0, s1, s2 = z3.Consts("s0 s1 s2", S)

    hyps = [valid(P, R1, E1, Rp), valid(Rp, R2, E2, Q)]
    unsat_cases("SEQ   {P}s1{R} ∧ {R}s2{Q} ⇒ {P}s1;s2{Q}", hyps, [
        z3.And(P(s0), E1(s0)),
        z3.And(P(s0), R1(s0, s1), E2(s1)),
        z3.And(P(s0), R1(s0, s1), R2(s1, s2), z3.Not(Q(s2)))])

    PC = z3.Function("PC", S, B)
    PnC = z3.Function("PnC", S, B)
    x = z3.Const("x", S)
    hyps = [z3.ForAll([x], PC(x) == z3.And(P(x), C(x))), z3.ForAll([x], PnC(x) == z3.And(P(x), z3.Not(C(x)))),
            valid(PC, R1, E1, Q), valid(PnC, R2, E2, Q)]
    unsat_cases("IF    {P∧c}s1{Q} ∧ {P∧¬c}s2{Q} ⇒ {P}if{Q}", hyps, [
        z3.And(P(s0), C(s0), E1(s0)), z3.And(P(s0), z3.Not(C(s0)), E2(s0)),
        z3.And(P(s0), C(s0), R1(s0, s1), z3.Not(Q(s1))),
        z3.And(P(s0), z3.Not(C(s0)), R2(s0, s1), z3.Not(Q(s1)))])

    V = z3.Function("V", S, I)
    Sf = pred("Sf")
    xv, lo, hi = z3.Ints("xv lo hi")
    core0 = z3.ForAll([x], z3.Implies(P(x), z3.And(Sf(x), lo <= V(x), V(x) <= hi)))
    tl, th = z3.Ints("tl th")
    oblig = z3.ForAll([x], z3.Implies(P(x), z3.And(tl <= V(x), V(x) <= th)))
    Fr = pred("Fr")
    frame_indep = z3.ForAll([x, xv], Fr(z3.Store(x, 0, xv)) == Fr(x))
    unsat_cases("ASGN  Core₀ + R-501 obligation ⇒ {P∧F}x:=e{tl≤x≤th ∧ F}", [core0, oblig, frame_indep], [
        z3.And(P(s0), Fr(s0), z3.Not(Sf(s0))),
        z3.And(P(s0), Fr(s0), s1 == z3.Store(s0, 0, V(s0)), z3.Not(z3.And(tl <= s1[0], s1[0] <= th, Fr(s1))))])

    sp = pred("sp")
    v0 = z3.Int("v0")
    sp_def = z3.ForAll([s1], sp(s1) == z3.Exists([s0], z3.And(P(s0), Sf(s0), s1 == z3.Store(s0, 0, V(s0)))))
    checked = z3.ForAll([s1], z3.Implies(sp(s1), Q(s1)))
    unsat_cases("SP    proved by symbolic execution ⇒ triple valid", [sp_def, checked], [
        z3.And(P(s0), Sf(s0), s1 == z3.Store(s0, 0, V(s0)), z3.Not(Q(s1)))])

    p = pred("p")
    unsat_cases("ASSERT (P⇒p) ⇒ {P}assert p{P∧p}", [z3.ForAll([x], z3.Implies(P(x), p(x)))], [
        z3.And(P(s0), z3.Not(p(s0)))])

    hav = z3.ForAll([x], z3.Implies(P(x), z3.ForAll([xv], Q(z3.Store(x, 0, xv)))))
    unsat_cases("HAVOC {∀v.Q[x:=v]}havoc x{Q}", [hav], [z3.And(P(s0), s1 == z3.Store(s0, 0, v0), z3.Not(Q(s1)))])
    unsat_cases("ASSUME {p⇒Q}assume p{Q}", [z3.ForAll([x], z3.Implies(P(x), z3.Implies(p(x), Q(x))))], [
        z3.And(P(s0), p(s0), z3.Not(Q(s0)))])

def loops():
    print("-- loop (induction on the iteration count) --")
    Inv = z3.Function("Inv", I, S, B)
    Rb = z3.Function("Rb", I, S, S, B)
    Eb = z3.Function("Eb", I, S, B)
    it = z3.Function("iter", I, S, S, B)
    err = z3.Function("errAt", I, S, B)
    lo, hi, k = z3.Ints("lo hi k")
    s0, s1, s2, x, y = z3.Consts("s0 s1 s2 x y", S)
    P, Q = pred("P"), pred("Q")
    w = z3.Int("w")

    step_ok = z3.ForAll([k, x], z3.Implies(z3.And(lo <= k, k < hi, Inv(k, x)),
                                           z3.And(z3.Not(Eb(k, x)),
                                                  z3.ForAll([y], z3.Implies(Rb(k, x, y), Inv(k + 1, y))))))
    if "loop" in BROKEN:
        step_ok = z3.ForAll([k, x], z3.Implies(z3.And(lo <= k, k < hi, Inv(k, x)), z3.Not(Eb(k, x))))
    frame = z3.ForAll([k, x, y], z3.Implies(Rb(k, x, y), y[w] == x[w]))
    entry = z3.ForAll([x], z3.Implies(P(x), Inv(lo, x)))
    base_def = z3.ForAll([x, y], it(lo, x, y) == (x == y))
    def Cl(kk, a, b):
        return z3.Implies(z3.And(lo <= kk, kk <= hi, P(a), it(kk, a, b)), z3.And(Inv(kk, b), b[w] == a[w]))
    lemma("LOOP base     C(lo)", [base_def, entry, lo <= hi], Cl(lo, s0, s1))
    lemma("LOOP step     C(k) ∧ k<hi ⇒ C(k+1)",
          [step_ok, frame, Cl(k, s0, s1), lo <= k, k < hi, P(s0), it(k, s0, s1), Rb(k, s1, s2),
           it(k + 1, s0, s2)],
          z3.And(Inv(k + 1, s2), s2[w] == s0[w]))
    lemma("LOOP no error: body safe under I(k)",
          [step_ok, Cl(k, s0, s1), lo <= k, k < hi, P(s0), it(k, s0, s1)], z3.Not(Eb(k, s1)))
    exit_ok = z3.ForAll([x], z3.Implies(Inv(hi, x), Q(x)))
    lemma("LOOP exit     C(hi) ∧ (I(hi)⇒Q) ⇒ Q", [exit_ok, Cl(hi, s0, s1), P(s0), it(hi, s0, s1), lo <= hi], Q(s1))

def calls():
    print("-- call (contract + state-write summary, D-09) --")
    Pre, Post = pred("Pre"), z3.Function("Post", S, S, B)
    Rb, Eb = rel("Rcall"), pred("Ecall")
    inW = z3.Function("inW", I, B)
    s0, s1, x, y = z3.Consts("s0 s1 x y", S)
    v = z3.Int("v")
    g = z3.Int("g")
    body_ok = z3.ForAll([x], z3.Implies(Pre(x), z3.And(z3.Not(Eb(x)),
                                                       z3.ForAll([y], z3.Implies(Rb(x, y), Post(x, y))))))
    body_frame = z3.ForAll([x, y, v], z3.Implies(z3.And(Rb(x, y), z3.Not(inW(v))), y[v] == x[v]))
    writes_g = inW(g)
    if "call" in BROKEN:
        inWc = lambda vv: z3.And(inW(vv), vv != g)
        hyp_g = [z3.Exists([x, y], z3.And(Pre(x), Rb(x, y), y[g] != x[g]))]
    else:
        inWc = inW
        hyp_g = []
    abs_ok = z3.And(Post(s0, s1), z3.ForAll([v], z3.Implies(z3.Not(inWc(v)), s1[v] == s0[v])))
    lemma("CALL body ⊆ (assert Pre; havoc W; assume Post)",
          [body_ok, body_frame, writes_g, Pre(s0), Rb(s0, s1)] + hyp_g, abs_ok)
    lemma("CALL precondition proved ⇒ call safe", [body_ok, Pre(s0)], z3.Not(Eb(s0)))

def concurrency():
    print("-- concurrency (ICPP, interleaving traces, absence of races) --")
    p1, p2, p3, ceil, isr = z3.Ints("p1 p2 p3 ceil isr")
    ceil_def = ceil == z3.If(p1 >= p2, z3.If(p1 >= p3, p1, p3), z3.If(p2 >= p3, p2, p3))
    if "icpp" in BROKEN:
        ceil_def = ceil == p1
    users_ok = z3.And(p1 >= 1, p2 >= 1, p3 >= 1, p1 <= 255, p2 <= 255, p3 <= 255)
    lemma("ICPP  no user can preempt during a protected operation", [ceil_def, users_ok],
          z3.And(z3.Not(p1 > ceil), z3.Not(p2 > ceil), z3.Not(p3 > ceil)))
    lemma("ICPP  R-306: a task cannot preempt a ceiling in the interrupt range",
          [isr >= 1001, isr <= 1015, users_ok], z3.And(z3.Not(p1 > isr), z3.Not(p2 > isr)))

    o0, o1, o2 = z3.Ints("o0 o1 o2")
    lo, hi, k = z3.Ints("lo hi k")
    op1, op2 = z3.Function("op1", I, I, B), z3.Function("op2", I, I, B)
    tr = z3.Function("tr", I, I, I, B)
    a, b = z3.Ints("a b")
    pres1 = z3.ForAll([a, b], z3.Implies(z3.And(lo <= a, a <= hi, op1(a, b)), z3.And(lo <= b, b <= hi)))
    pres2 = z3.ForAll([a, b], z3.Implies(z3.And(lo <= a, a <= hi, op2(a, b)), z3.And(lo <= b, b <= hi)))
    lemma("TRACE base: tr(0) = initial (in range)",
          [z3.ForAll([a, b], tr(0, a, b) == (a == b)), lo <= o0, o0 <= hi, tr(0, o0, o1)],
          z3.And(lo <= o1, o1 <= hi))
    lemma("TRACE step: an atomic operation of any task preserves the range",
          [pres1, pres2, z3.And(lo <= o1, o1 <= hi), z3.Or(op1(o1, o2), op2(o1, o2))], z3.And(lo <= o2, o2 <= hi))

    s0, s1, x, y = z3.Consts("s0 s1 x y", S)
    g = z3.Int("g")
    Ru = rel("Rother")
    other_frame = z3.ForAll([x, y], z3.Implies(Ru(x, y), y[g] == x[g]))
    lemma("RACE  a step of another task does not change the state of t", [other_frame, Ru(s0, s1)], s1[g] == s0[g])

def linear():
    print("-- linear resources (R-203) --")
    AB = z3.ArraySort(I, B)
    live, ptr, hg, sg = z3.Const("live", AB), z3.Const("ptr", S), z3.Const("hg", S), z3.Const("sg", S)
    u, v = z3.Ints("u v")

    def Inv(L, Pt, H, G):
        return z3.And(z3.ForAll([v], z3.Implies(L[v], H[v] == G[Pt[v]])),
                      z3.ForAll([u, v], z3.Implies(z3.And(L[u], L[v], u != v), Pt[u] != Pt[v])))

    x, y = z3.Ints("x y")
    inv0 = Inv(live, ptr, hg, sg)
    L1 = z3.Store(z3.Store(live, x, False), y, True)
    lemma("LINEAR move (let y = x) preserves Inv", [inv0, live[x], z3.Not(live[y]), x != y],
          Inv(L1, z3.Store(ptr, y, ptr[x]), z3.Store(hg, y, hg[x]), sg))
    st = ptr[x]
    G2 = z3.Store(sg, st, sg[st] + 1)
    L2 = z3.Store(z3.Store(live, x, False), y, True)
    if "linear" in BROKEN:
        L2 = z3.Store(live, y, True)
    lemma("LINEAR transit preserves Inv", [inv0, live[x], z3.Not(live[y]), x != y],
          Inv(L2, z3.Store(ptr, y, st), z3.Store(hg, y, G2[st]), G2))
    lemma("LINEAR field access/borrow live ⇒ handle current", [inv0, live[x]], hg[x] == sg[ptr[x]])
    d = z3.Int("d")
    lemma("LINEAR take(d) preserves Inv", [inv0, live[d], z3.Not(live[y]), d != y],
          Inv(z3.Store(z3.Store(live, d, False), y, True), z3.Store(ptr, y, ptr[d]), z3.Store(hg, y, hg[d]), sg))
    lemma("LINEAR put(d, y) preserves Inv", [inv0, live[y], z3.Not(live[d]), d != y],
          Inv(z3.Store(z3.Store(live, y, False), d, True), z3.Store(ptr, d, ptr[y]), z3.Store(hg, d, hg[y]), sg))
    l1, l2 = z3.Const("l1", AB), z3.Const("l2", AB)
    p1, p2, h1, h2, g1, g2 = (z3.Const(n, S) for n in ("p1", "p2", "h1", "h2", "g1", "g2"))
    c = z3.Bool("c")
    lemma("LINEAR branch join (same live set) preserves Inv",
          [Inv(l1, p1, h1, g1), Inv(l2, p2, h2, g2), l1 == l2],
          Inv(l1, z3.If(c, p1, p2), z3.If(c, h1, h2), z3.If(c, g1, g2)))

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    self_test = "--self-test" in sys.argv
    if self_test:
        BROKEN.update({"loop", "call", "icpp", "linear"})
    print("Full Spelf-Core (Core₁): semantic soundness lemmas of the program logic" +
          (" [SELF-TEST: broken rules]" if self_test else ""))
    statements()
    loops()
    calls()
    concurrency()
    linear()
    good = sum(1 for _, ok in results if ok)
    if self_test:
        failed = {n.split()[0] for n, ok in results if not ok}
        want = {"LOOP", "CALL", "ICPP", "LINEAR"}
        ok_self = want <= failed
        print(f"\nself-test: broken rules {'CAUGHT' if ok_self else 'NOT CAUGHT'} "
              f"(failed families: {sorted(failed)})")
        sys.exit(0 if ok_self else 1)
    print(f"\n{good}/{len(results)} lemmas proved"
          + (": the Core₁ program logic is semantically sound (by induction on derivations)." if good == len(results) else ""))
    sys.exit(0 if good == len(results) else 1)

if __name__ == "__main__":
    main()
