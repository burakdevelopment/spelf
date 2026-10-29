import sys
import time

import z3

MUT = 0
MUT_DIV = False

def main():
    global MUT, MUT_DIV
    if "--self-test" in sys.argv:
        MUT, MUT_DIV = 1, True
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    I = z3.IntSort()
    A = z3.ArraySort(I, I)

    E = z3.Datatype("Expr")
    E.declare("Lit", ("n", I))
    E.declare("Var", ("x", I))
    for op in ("Add", "Sub", "Mul", "Div"):
        E.declare(op, (op.lower() + "_l", E), (op.lower() + "_r", E))
    E.declare("IfLt", ("if_a", E), ("if_b", E), ("if_t", E), ("if_f", E))
    E.declare("Let", ("let_v", I), ("let_d", E), ("let_body", E))
    E = E.create()

    T = z3.Datatype("Ty")
    T.declare("mk", ("ok", z3.BoolSort()), ("lo", I), ("hi", I))
    T = T.create()
    ok, lo, hi = T.ok, T.lo, T.hi

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

    def tdiv(x, y):
        ax, ay = z3.If(x >= 0, x, -x), z3.If(y >= 0, y, -y)
        q = ax / ay
        return z3.If(z3.Or(z3.And(x >= 0, y > 0), z3.And(x < 0, y < 0)), q, -q)

    ty = z3.Function("ty", E, A, A, T)
    ev = z3.Function("ev", E, A, I)
    safe = z3.Function("safe", E, A, z3.BoolSort())

    e, glo, ghi, rho = z3.Const("e", E), z3.Const("glo", A), z3.Const("ghi", A), z3.Const("rho", A)

    def T2(k, a, b):
        ta, tb = ty(a, glo, ghi), ty(b, glo, ghi)
        la, ha, lb, hb = lo(ta), hi(ta), lo(tb), hi(tb)
        both = z3.And(ok(ta), ok(tb))
        if k == "Add":
            return T.mk(both, la + lb, ha + hb - MUT)
        if k == "Sub":
            return T.mk(both, la - hb, ha - lb)
        if k == "Mul":
            c = [la * lb, la * hb, ha * lb, ha * hb]
            return T.mk(both, zmin(*c), zmax(*c))
        if k == "Div" and MUT_DIV:
            c = [tdiv(la, lb), tdiv(la, hb), tdiv(ha, lb), tdiv(ha, hb)]
            return T.mk(both, zmin(*c), zmax(*c))
        c = [tdiv(la, lb), tdiv(la, hb), tdiv(ha, lb), tdiv(ha, hb)]
        return T.mk(z3.And(both, z3.Or(lb > 0, hb < 0)), zmin(*c), zmax(*c))

    def by_cases(fn_expr_builder, default):
        body = default
        return body

    ty_body = z3.If(E.is_Lit(e), T.mk(True, E.n(e), E.n(e)),
              z3.If(E.is_Var(e), T.mk(glo[E.x(e)] <= ghi[E.x(e)], glo[E.x(e)], ghi[E.x(e)]),
              z3.If(E.is_Add(e), T2("Add", E.add_l(e), E.add_r(e)),
              z3.If(E.is_Sub(e), T2("Sub", E.sub_l(e), E.sub_r(e)),
              z3.If(E.is_Mul(e), T2("Mul", E.mul_l(e), E.mul_r(e)),
              z3.If(E.is_Div(e), T2("Div", E.div_l(e), E.div_r(e)),
              z3.If(E.is_IfLt(e),
                    (lambda tt, tf, ta, tb: T.mk(z3.And(ok(ta), ok(tb), ok(tt), ok(tf)),
                                                 zmin(lo(tt), lo(tf)), zmax(hi(tt), hi(tf))))(
                        ty(E.if_t(e), glo, ghi), ty(E.if_f(e), glo, ghi), ty(E.if_a(e), glo, ghi), ty(E.if_b(e), glo, ghi)),
                    (lambda td: (lambda tb: T.mk(z3.And(ok(td), ok(tb)), lo(tb), hi(tb)))(
                        ty(E.let_body(e), z3.Store(glo, E.let_v(e), lo(td)), z3.Store(ghi, E.let_v(e), hi(td)))))(
                        ty(E.let_d(e), glo, ghi)))))))))

    ev_body = z3.If(E.is_Lit(e), E.n(e),
              z3.If(E.is_Var(e), rho[E.x(e)],
              z3.If(E.is_Add(e), ev(E.add_l(e), rho) + ev(E.add_r(e), rho),
              z3.If(E.is_Sub(e), ev(E.sub_l(e), rho) - ev(E.sub_r(e), rho),
              z3.If(E.is_Mul(e), ev(E.mul_l(e), rho) * ev(E.mul_r(e), rho),
              z3.If(E.is_Div(e), tdiv(ev(E.div_l(e), rho), ev(E.div_r(e), rho)),
              z3.If(E.is_IfLt(e), z3.If(ev(E.if_a(e), rho) < ev(E.if_b(e), rho), ev(E.if_t(e), rho), ev(E.if_f(e), rho)),
                    ev(E.let_body(e), z3.Store(rho, E.let_v(e), ev(E.let_d(e), rho))))))))))

    safe_body = z3.If(z3.Or(E.is_Lit(e), E.is_Var(e)), True,
                z3.If(E.is_Add(e), z3.And(safe(E.add_l(e), rho), safe(E.add_r(e), rho)),
                z3.If(E.is_Sub(e), z3.And(safe(E.sub_l(e), rho), safe(E.sub_r(e), rho)),
                z3.If(E.is_Mul(e), z3.And(safe(E.mul_l(e), rho), safe(E.mul_r(e), rho)),
                z3.If(E.is_Div(e), z3.And(safe(E.div_l(e), rho), safe(E.div_r(e), rho), ev(E.div_r(e), rho) != 0),
                z3.If(E.is_IfLt(e), z3.And(safe(E.if_a(e), rho), safe(E.if_b(e), rho),
                                          z3.If(ev(E.if_a(e), rho) < ev(E.if_b(e), rho), safe(E.if_t(e), rho),
                                                safe(E.if_f(e), rho))),
                      z3.And(safe(E.let_d(e), rho),
                             safe(E.let_body(e), z3.Store(rho, E.let_v(e), ev(E.let_d(e), rho))))))))))

    def models(r, gl, gh):
        i = z3.Int("i")
        return z3.ForAll([i], z3.And(gl[i] <= r[i], r[i] <= gh[i]))

    def P(t, gl, gh, r):
        tt = ty(t, gl, gh)
        return z3.Implies(z3.And(models(r, gl, gh), ok(tt)),
                          z3.And(safe(t, r), lo(tt) <= ev(t, r), ev(t, r) <= hi(tt)))

    results = []

    def step(name, term, instances):
        s = z3.Solver()
        s.set("timeout", 300_000)
        for sub, gl, gh, r in instances:
            s.add(P(sub, gl, gh, r))
        s.add(ty(term, glo, ghi) == z3.simplify(z3.substitute(ty_body, (e, term))))
        s.add(ev(term, rho) == z3.simplify(z3.substitute(ev_body, (e, term))))
        s.add(safe(term, rho) == z3.simplify(z3.substitute(safe_body, (e, term))))
        s.add(z3.Not(P(term, glo, ghi, rho)))
        t0 = time.time()
        r = s.check()
        dt = time.time() - t0
        results.append((name, r == z3.unsat, r))
        print(f"{'PROVED' if r == z3.unsat else 'FAILED':9} induction step {name:10} {dt:7.2f} s" +
              ("" if r == z3.unsat else f"  [{r}]"))

    a, b, c, d = (z3.Const(n, E) for n in ("a", "b", "c", "d"))
    n, x = z3.Int("n"), z3.Int("x")
    print("Core₀ soundness: structural induction (one step per constructor)")
    same = lambda *ts: [(t, glo, ghi, rho) for t in ts]
    step("Lit", E.Lit(n), [])
    step("Var", E.Var(x), [])
    step("Add", E.Add(a, b), same(a, b))
    step("Sub", E.Sub(a, b), same(a, b))
    step("Mul", E.Mul(a, b), same(a, b))
    step("Div", E.Div(a, b), same(a, b))
    step("IfLt", E.IfLt(a, b, c, d), same(a, b, c, d))
    td = ty(a, glo, ghi)
    step("Let", E.Let(x, a, b), same(a) + [(b, z3.Store(glo, x, lo(td)), z3.Store(ghi, x, hi(td)),
                                            z3.Store(rho, x, ev(a, rho)))])
    good = sum(1 for _, g, _ in results if g)
    if MUT:
        failed = {n for n, g, _ in results if not g}
        ok_self = failed == {"Add", "Div"}
        print(f"\nself-test: broken Add and Div rules {'CAUGHT' if ok_self else 'NOT CAUGHT'} "
              f"(failed steps: {sorted(failed)})")
        sys.exit(0 if ok_self else 1)
    print(f"\n{good}/{len(results)} induction steps proved"
          + (": the Core₀ soundness theorem holds, mechanised (by the principle of structural induction)."
             if good == len(results) else ""))
    sys.exit(0 if good == len(results) else 1)

if __name__ == "__main__":
    main()
