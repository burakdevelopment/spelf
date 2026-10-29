from fractions import Fraction

import z3

U = z3.RealVal("1/9007199254740992")
ETA = z3.RealVal("1/" + str(2 ** 1074))
DBL_MAX = Fraction(2) ** 1024 - Fraction(2) ** 971

_ARITH = {z3.Z3_OP_FPA_ADD: lambda a, b: a + b, z3.Z3_OP_FPA_SUB: lambda a, b: a - b,
          z3.Z3_OP_FPA_MUL: lambda a, b: a * b, z3.Z3_OP_FPA_DIV: lambda a, b: a / b}
_CMP = {z3.Z3_OP_FPA_LE: lambda a, b: a <= b, z3.Z3_OP_FPA_LT: lambda a, b: a < b,
        z3.Z3_OP_FPA_GE: lambda a, b: a >= b, z3.Z3_OP_FPA_GT: lambda a, b: a > b,
        z3.Z3_OP_FPA_EQ: lambda a, b: a == b}

class Unsupported(Exception):
    pass

def _frac(v):
    r = z3.simplify(z3.fpToReal(v))
    return Fraction(r.numerator_as_long(), r.denominator_as_long())

def var_bounds(facts) -> dict:
    lo, hi = {}, {}
    for f in facts:
        if not z3.is_app(f):
            continue
        k = f.decl().kind()
        if k in (z3.Z3_OP_FPA_GE, z3.Z3_OP_FPA_LE) and f.num_args() == 2:
            for x, c, is_lower in ((f.arg(0), f.arg(1), k == z3.Z3_OP_FPA_GE),
                                   (f.arg(1), f.arg(0), k == z3.Z3_OP_FPA_LE)):
                if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED and z3.is_fp_value(c) \
                        and not (c.isNaN() or c.isInf()):
                    (lo if is_lower else hi)[x.decl().name()] = _frac(c)
    return {n: (lo[n], hi[n]) for n in lo if n in hi}

class Relaxer:
    def __init__(self, bounds: dict | None = None, linear: bool = False):
        self.bounds = bounds or {}
        self.linear = linear
        self.ivs = {}
        self.cache = {}
        self.vars = {}
        self.side = []
        self.overflow = []
        self.rounded = {}
        self.n = 0

    def fresh(self):
        self.n += 1
        return z3.Real(f"_fl!{self.n}")

    @staticmethod
    def exact(v):
        if v.isNaN() or v.isInf():
            raise Unsupported("NaN/∞ constant")
        r = z3.simplify(z3.fpToReal(v))
        return r

    def _iv(self, x):
        if z3.is_rational_value(x):
            q = Fraction(x.numerator_as_long(), x.denominator_as_long())
            return (q, q)
        hit = self.ivs.get(x.get_id())
        if hit is not None:
            return hit
        if z3.is_app(x) and x.decl().kind() == z3.Z3_OP_ITE:
            a, b = self._iv(x.arg(1)), self._iv(x.arg(2))
            if a is not None and b is not None:
                return (min(a[0], b[0]), max(a[1], b[1]))
        return None

    @staticmethod
    def _iv_op(k, ia, ib):
        if ia is None or ib is None:
            return None
        if k == z3.Z3_OP_FPA_ADD:
            return (ia[0] + ib[0], ia[1] + ib[1])
        if k == z3.Z3_OP_FPA_SUB:
            return (ia[0] - ib[1], ia[1] - ib[0])
        if k == z3.Z3_OP_FPA_MUL:
            c = [x * y for x in ia for y in ib]
            return (min(c), max(c))
        if k == z3.Z3_OP_FPA_DIV and (ib[0] > 0 or ib[1] < 0):
            c = [x / y for x in ia for y in ib]
            return (min(c), max(c))
        return None

    def monotone(self, a, b):
        for x, c in ((a, b), (b, a)):
            if not z3.is_rational_value(c):
                continue
            for leaf in self._ite_leaves(x):
                hit = self.rounded.get(leaf.get_id())
                if hit is not None and hit[0].eq(leaf):
                    r, e = hit
                    self.side += [z3.Implies(e <= c, r <= c), z3.Implies(e >= c, r >= c)]

    @staticmethod
    def _ite_leaves(x):
        out, stack = [], [x]
        while stack:
            y = stack.pop()
            if isinstance(y, z3.ExprRef) and z3.is_app(y) and y.decl().kind() == z3.Z3_OP_ITE:
                stack += [y.arg(1), y.arg(2)]
            elif isinstance(y, z3.ExprRef):
                out.append(y)
        return out

    def conv(self, t):
        key = t.get_id()
        hit = self.cache.get(key)
        if hit is not None and hit[0].eq(t):
            return hit[1]
        r = self._conv(t)
        self.cache[key] = (t, r)
        return r

    def _conv(self, t):
        if z3.is_fp_value(t):
            return self.exact(t)
        k = t.decl().kind() if z3.is_app(t) else None
        if z3.is_fp(t) and z3.is_const(t) and k == z3.Z3_OP_UNINTERPRETED:
            name = t.decl().name()
            if name not in self.vars:
                self.vars[name] = z3.Real(name.replace("|", "_").replace("\\", "_") + "~r")
                if name in self.bounds:
                    self.ivs[self.vars[name].get_id()] = self.bounds[name]
            return self.vars[name]
        if k in _ARITH:
            a, b = self.conv(t.arg(1)), self.conv(t.arg(2))
            ie = self._iv_op(k, self._iv(a), self._iv(b))
            sb, eb = t.sort().sbits(), t.sort().ebits()
            u_q = Fraction(1, 2 ** sb)
            eta_q = Fraction(1, 2 ** (sb - 1 + 2 ** (eb - 1) - 2))
            max_q = (2 - Fraction(1, 2 ** (sb - 1))) * Fraction(2) ** (2 ** (eb - 1) - 1)
            if self.linear and k in (z3.Z3_OP_FPA_MUL, z3.Z3_OP_FPA_DIV) and ie is not None                     and not z3.is_rational_value(a) and not z3.is_rational_value(b):
                e = self.fresh()
                self.side += [e >= z3.RealVal(f"{ie[0].numerator}/{ie[0].denominator}"),
                              e <= z3.RealVal(f"{ie[1].numerator}/{ie[1].denominator}")]
            else:
                e = _ARITH[k](a, b)
            r = self.fresh()
            d = r - e
            if ie is not None:
                M = max(abs(ie[0]), abs(ie[1]))
                err = u_q * M + eta_q
                bound = z3.RealVal(f"{err.numerator}/{err.denominator}")
                self.side += [d <= bound, -d <= bound]
                self.ivs[r.get_id()] = (ie[0] - err, ie[1] + err)
                if M <= max_q / 2:
                    pass
                else:
                    Mz = z3.RealVal(f"{max_q.numerator}/{max_q.denominator}")
                    self.overflow.append(z3.And(e <= Mz, -e <= Mz))
            else:
                Uz = z3.RealVal(f"{u_q.numerator}/{u_q.denominator}")
                Ez = z3.RealVal(f"{eta_q.numerator}/{eta_q.denominator}")
                self.side.append(z3.Or(z3.And(e >= 0, d <= Uz * e + Ez, -d <= Uz * e + Ez),
                                       z3.And(e < 0, d <= -Uz * e + Ez, -d <= -Uz * e + Ez)))
                Mz = z3.RealVal(f"{max_q.numerator}/{max_q.denominator}")
                self.overflow.append(z3.And(e <= Mz, -e <= Mz))
            self.side += [z3.Implies(e >= 0, r >= 0), z3.Implies(e <= 0, r <= 0)]
            self.rounded[r.get_id()] = (r, e)
            return r
        if k == z3.Z3_OP_FPA_TO_FP and t.num_args() == 2 and z3.is_fp(t.arg(1)):
            x = self.conv(t.arg(1))
            sb, eb = t.sort().sbits(), t.sort().ebits()
            ix = self._iv(x)
            r = self.fresh()
            u_q = Fraction(1, 2 ** sb)
            eta_q = Fraction(1, 2 ** (sb - 1 + 2 ** (eb - 1) - 2))
            if ix is not None:
                M = max(abs(ix[0]), abs(ix[1]))
                err = u_q * M + eta_q
                bz = z3.RealVal(f"{err.numerator}/{err.denominator}")
                self.side += [r - x <= bz, x - r <= bz]
                self.ivs[r.get_id()] = (ix[0] - err, ix[1] + err)
            else:
                raise Unsupported("format conversion without a range")
            self.side += [z3.Implies(x >= 0, r >= 0), z3.Implies(x <= 0, r <= 0)]
            self.rounded[r.get_id()] = (r, x)
            return r
        if k == z3.Z3_OP_FPA_NEG:
            x = self.conv(t.arg(0))
            r = -x
            ix = self._iv(x)
            if ix is not None:
                self.ivs[r.get_id()] = (-ix[1], -ix[0])
            return r
        if k == z3.Z3_OP_FPA_ABS:
            x = self.conv(t.arg(0))
            return z3.If(x >= 0, x, -x)
        if k in _CMP:
            a, b = self.conv(t.arg(0)), self.conv(t.arg(1))
            self.monotone(a, b)
            return _CMP[k](a, b)
        if k == z3.Z3_OP_FPA_IS_ZERO:
            return self.conv(t.arg(0)) == 0
        if k in (z3.Z3_OP_FPA_IS_NAN, z3.Z3_OP_FPA_IS_INF):
            return z3.BoolVal(False)
        if k in (z3.Z3_OP_FPA_IS_NORMAL, z3.Z3_OP_FPA_IS_SUBNORMAL):
            raise Unsupported(str(t.decl()))
        if k == z3.Z3_OP_FPA_TO_REAL:
            return self.conv(t.arg(0))
        if k == z3.Z3_OP_EQ and z3.is_fp(t.arg(0)):
            return self.conv(t.arg(0)) == self.conv(t.arg(1))
        if k == z3.Z3_OP_DISTINCT and z3.is_fp(t.arg(0)):
            return self.conv(t.arg(0)) != self.conv(t.arg(1))
        if k == z3.Z3_OP_ITE:
            c, a, b = self.conv(t.arg(0)), self.conv(t.arg(1)), self.conv(t.arg(2))
            r = z3.If(c, a, b)
            ia, ib = self._iv(a), self._iv(b)
            if ia is not None and ib is not None:
                self.ivs[r.get_id()] = (min(ia[0], ib[0]), max(ia[1], ib[1]))
            return r
        if z3.is_fp(t) or (z3.is_app(t) and k is not None and 45000 <= k < 45200):
            raise Unsupported(str(t.decl()))
        if not z3.is_app(t) or t.num_args() == 0:
            return t
        kids = [self.conv(c) for c in t.children()]
        if all(a.eq(b) for a, b in zip(kids, t.children())):
            return t
        if k == z3.Z3_OP_ITE:
            return z3.If(kids[0], kids[1], kids[2])
        if k == z3.Z3_OP_AND:
            return z3.And(*kids)
        if k == z3.Z3_OP_OR:
            return z3.Or(*kids)
        if k == z3.Z3_OP_NOT:
            return z3.Not(kids[0])
        if k == z3.Z3_OP_IMPLIES:
            return z3.Implies(kids[0], kids[1])
        if k == z3.Z3_OP_EQ:
            return kids[0] == kids[1]
        return t.decl()(*kids)

def relaxed_query(facts, pc, goal, linear: bool = False):
    rx = Relaxer(var_bounds(facts), linear)
    F = [rx.conv(f) for f in facts] + [rx.conv(p) for p in pc]
    g = rx.conv(goal)
    return F + rx.side, z3.Not(z3.And(g, *rx.overflow))
