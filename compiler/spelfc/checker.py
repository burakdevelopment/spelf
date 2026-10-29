import math
from dataclasses import dataclass

import z3
from z3.z3util import get_vars

from . import ast as A
from . import fprelax
from . import units as U
from .diagnostics import SpelfError
from .stypes import (CLAMP_TAGS, HELD_TAGS, INT64_MAX, INT64_MIN, PUSH_TAGS, SUSPECT_TAGS, ArrayT,
                    BoolT, BoundedT, ClampedT, EnumT, FixT, FloatT, HeldReadT, HeldT, InstantT, IntT,
                    LatchT, RecordT, WireT, ResT, BorrowT,
                    PushResT, StrT, SuspectT, UnitT, fits_int64, structurally_equal)

ROUND = ("nearest_even", "toward_zero", "floor", "ceil")

RLIMIT = 20_000_000

RESERVED = {"print", "push", "empty", "bits", "saturate", "decode", "refresh", "read", "feed",
            "is_set", "reset", "held", "latch", "wire", "encode", "requantize", "narrow", "convert",
            "take", "put", "transit", "xor", "band", "bor", "shl", "shr", "rotate"}

@dataclass
class Val:
    ty: object
    term: object
    taint: bool = False

@dataclass
class Binding:
    ty: object
    term: object
    mutable: bool
    kind: str = "local"
    taint: bool = False

@dataclass
class Sig:
    fn: A.FnDecl
    params: list
    ret: object
    modes: frozenset
    requires: list
    valid_for: list
    validated_by: str | None
    ensures: list
    result_sym: object

FLOAT_MODELS = ("ieee", "real")

class Checker:
    def __init__(self, prog: A.Program, filename: str, float_model: str = "ieee"):
        self.prog = prog
        self.filename = filename
        assert float_model in FLOAT_MODELS
        self.float_model = float_model
        self.fp = float_model == "ieee"
        self.fsort = z3.Float64()
        self.enums: dict[str, list[str]] = {}
        self.consts: dict[str, Val] = {}
        self.configs: dict[str, dict[str, tuple]] = {}
        self.fns: dict[str, A.FnDecl] = {}
        self.sigs: dict[str, Sig] = {}
        self.modes: set[str] = set()
        self.flight: set[str] = set()
        self.module = None
        self.facts: list = []
        self.pc: list = []
        self.fresh_n = 0
        self.sym_desc: dict[str, str] = {}
        self.stats: dict[str, int] = {}
        self.notes: list[str] = []
        self.cur: Sig | None = None
        self.suspect_types: set = set()
        self.bounded_types: set = set()
        self.array_types: set = set()
        self.states: dict[str, dict] = {}
        self.hypotheses: dict[str, A.FaultHypothesis] = {}
        self.encodings: dict[str, dict] = {}
        self.protected: dict[str, dict] = {}
        self.structs: dict[str, dict] = {}
        self.redundancy: dict[str, A.RedundancyDecl] = {}
        self._sw_cache: dict[str, frozenset] = {}
        self.bmc_mode = False
        self.bmc_returns = None
        self.evidence: list | None = None
        self.tasks: dict[str, dict] = {}
        self.ceilings: dict[str, int] = {}
        self.clamp_types: set = set()
        self.pc_taint = False
        self.rounding_used: set = set()
        self.schedules: list = []
        self.mode_edges: list = []
        self.clock_decl = None
        self.watchdogs: dict = {}
        self.timing: dict = {}
        self.const_decls: dict = {}
        self.aliases: dict = {}
        self._alias_stack: list = []
        self.resources: dict = {}
        self.transitions: dict = {}
        self.regions: dict = {}
        self.rotations: dict = {}
        self._rot_n = 0

    def err(self, rule, msg, node=None, note=None):
        line = getattr(node, "line", 0)
        col = getattr(node, "col", 0)
        raise SpelfError(rule, msg, self.filename, line, col, note)

    def fresh_name(self, base: str) -> str:
        self.fresh_n += 1
        return f"{base}!{self.fresh_n}"

    def sym(self, ty, desc: str):
        if isinstance(ty, IntT):
            s = z3.Int(self.fresh_name(desc))
            self.sym_desc[str(s)] = desc
            self.facts += [s >= ty.lo, s <= ty.hi]
            return s
        if isinstance(ty, FloatT):
            s = self.fsym(desc, ty.bits)
            if ty.lo is not None:
                self.facts += [s >= self.fval(ty.lo, ty.bits), s <= self.fval(ty.hi, ty.bits)]
            elif self.fp:
                self.facts.append(z3.Not(z3.fpIsNaN(s)))
            return s
        if isinstance(ty, InstantT):
            s = self.fsym(desc)
            if self.fp:
                self.facts += [z3.Not(z3.fpIsNaN(s)), z3.Not(z3.fpIsInf(s))]
            return s
        if isinstance(ty, BoolT):
            s = z3.Bool(self.fresh_name(desc))
            self.sym_desc[str(s)] = desc
            return s
        if isinstance(ty, EnumT):
            s = z3.Int(self.fresh_name(desc))
            self.sym_desc[str(s)] = desc
            self.facts += [s >= 0, s < len(self.enums[ty.name])]
            return s
        if isinstance(ty, PushResT):
            s = z3.Int(self.fresh_name(desc))
            self.facts += [s >= 0, s <= 1]
            return s
        if isinstance(ty, FixT):
            s = z3.Int(self.fresh_name(desc))
            self.sym_desc[str(s)] = desc + " (ham)"
            self.facts += [s >= ty.lo, s <= ty.hi]
            return s
        if isinstance(ty, ClampedT):
            tag = z3.Int(self.fresh_name(desc + ".tag"))
            self.facts += [tag >= 0, tag <= 1]
            return (tag, self.sym(ty.inner, desc + ".value"))
        if isinstance(ty, RecordT):
            info = self.structs[ty.name]
            val = {f: self.sym(fty, f"{desc}.{f}") for f, fty in info["fields"]}
            if info["inv"] is not None:
                env = {f: Binding(fty, val[f], False, "param") for f, fty in info["fields"]}
                v = self.eval(info["inv"], env)
                self.facts.append(v.term)
            return val
        if isinstance(ty, WireT):
            enc = self.encodings[ty.name]
            s = z3.Int(self.fresh_name(desc))
            self.sym_desc[str(s)] = desc + " (wire)"
            self.facts += [s >= enc["wire"].lo, s <= enc["wire"].hi]
            return s
        if isinstance(ty, HeldReadT):
            tag = z3.Int(self.fresh_name(desc + ".tag"))
            self.facts += [tag >= 0, tag <= 1]
            return (tag, self.sym(ty.inner, desc + ".value"))
        if isinstance(ty, SuspectT):
            tag = z3.Int(self.fresh_name(desc + ".tag"))
            self.facts += [tag >= 0, tag <= 3]
            return (tag, self.sym(ty.inner, desc + ".value"))
        if isinstance(ty, (ResT, BorrowT)):
            rty = ty.inner if isinstance(ty, BorrowT) else ty
            info = self.resources.get(rty.name)
            if not info or not info.get("fields"):
                return None
            return {f: self.sym(fty, f"{desc}.{f}") for f, fty in info["fields"]}
        return None

    def relevant_facts(self, terms) -> list:
        want = set()
        for t in terms:
            want |= self._vars_of(t)
        by_var: dict = {}
        fvars = []
        for i, f in enumerate(self.facts):
            vs = self._vars_of(f)
            fvars.append(vs)
            for v in vs:
                by_var.setdefault(v, []).append(i)
        chosen = [False] * len(self.facts)
        todo = list(want)
        seen = set(want)
        while todo:
            v = todo.pop()
            for i in by_var.get(v, ()):
                if not chosen[i]:
                    chosen[i] = True
                    for w in fvars[i]:
                        if w not in seen:
                            seen.add(w)
                            todo.append(w)
        return [f for f, c in zip(self.facts, chosen) if c]

    def _vars_of(self, t) -> frozenset:
        cache = self.__dict__.setdefault("_var_cache", {})
        hit = cache.get(t.get_id())
        if hit is not None and hit[0].eq(t):
            return hit[1]
        out, stack, seen = set(), [t], set()
        while stack:
            x = stack.pop()
            xid = x.get_id()
            if xid in seen:
                continue
            seen.add(xid)
            c = cache.get(xid)
            if c is not None and c[0].eq(x):
                out |= c[1]
                continue
            if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
                out.add(str(x))
            else:
                stack.extend(x.children())
        r = frozenset(out)
        cache[t.get_id()] = (t, r)
        return r

    def _record(self, rule: str, node, msg: str, how: str, solver=None, goal=None):
        if self.bmc_mode and rule != "R-605":
            return
        self.stats[rule] = self.stats.get(rule, 0) + 1
        if self.evidence is not None:
            smt = None
            if solver is not None:
                smt = solver.to_smt2()
            elif goal is not None:
                q = z3.Solver()
                q.add(*self.relevant_facts([goal] + list(self.pc)))
                q.add(*self.pc)
                q.add(z3.Not(goal))
                smt = q.to_smt2()
            self.evidence.append({
                "rule": rule, "line": getattr(node, "line", 0), "col": getattr(node, "col", 0),
                "fn": self.cur.fn.name if self.cur is not None else None,
                "what": msg, "how": how, "smt2": smt,
            })

    @staticmethod
    def f32(v: float) -> float:
        import struct
        return struct.unpack("f", struct.pack("f", v))[0]

    def fsort_of(self, bits: int):
        return z3.Float32() if bits == 32 else z3.Float64()

    def fval(self, v, bits: int = 64):
        if self.fp:
            return z3.FPVal(repr(float(v)), self.fsort_of(bits))
        from fractions import Fraction
        q = Fraction(self.f32(float(v)) if bits == 32 else float(v))
        return z3.RealVal(f"{q.numerator}/{q.denominator}")

    U_ROUND = "1/9007199254740992"
    ETA = "1/" + str(2 ** 1074)

    def fround(self, e, lo=None, hi=None, bits: int = 64):
        if not getattr(self, "relaxed", False):
            return e
        r = z3.Real(self.fresh_name("_rounded"))
        u = z3.RealVal(self.U_ROUND if bits == 64 else "1/16777216")
        eta = z3.RealVal(self.ETA if bits == 64 else "1/" + str(2 ** 149))
        d = r - e
        if lo is not None and hi is not None:
            from fractions import Fraction
            M = max(abs(Fraction(lo)), abs(Fraction(hi)))
            bound = u * z3.RealVal(f"{M.numerator}/{M.denominator}") + eta
            self.facts += [d <= bound, -d <= bound]
        else:
            self.facts.append(z3.Or(z3.And(e >= 0, d <= u * e + eta, -d <= u * e + eta),
                                    z3.And(e < 0, d <= -u * e + eta, -d <= -u * e + eta)))
        self.facts += [z3.Implies(e >= 0, r >= 0), z3.Implies(e <= 0, r <= 0)]
        self.__dict__.setdefault("_rounded", {})[r.get_id()] = (r, e)
        return r

    def fround_monotone(self, x, y):
        rd = getattr(self, "_rounded", {})
        for a, b in ((x, y), (y, x)):
            hit = rd.get(a.get_id()) if isinstance(a, z3.ExprRef) else None
            if hit is not None and hit[0].eq(a) and z3.is_rational_value(z3.simplify(b)):
                r, e = hit
                self.facts += [z3.Implies(e <= b, r <= b), z3.Implies(e >= b, r >= b)]

    def fsym(self, desc: str, bits: int = 64):
        s = z3.FP(self.fresh_name(desc), self.fsort_of(bits)) if self.fp else z3.Real(self.fresh_name(desc))
        self.sym_desc[str(s)] = desc
        return s

    def freal(self, t):
        return z3.fpToReal(t) if self.fp and z3.is_fp(t) else t

    def _has_fp(self, terms) -> bool:
        cache = self.__dict__.setdefault("_fp_cache", {})
        stack, seen = list(terms), set()
        while stack:
            x = stack.pop()
            if not isinstance(x, z3.ExprRef):
                continue
            k = x.get_id()
            if k in seen:
                continue
            seen.add(k)
            hit = cache.get(k)
            if hit is not None and hit[0].eq(x):
                if hit[1]:
                    return True
                continue
            if z3.is_fp(x):
                cache[k] = (x, True)
                return True
            stack.extend(x.children())
        return False

    def sat_under_path(self, t) -> bool:
        facts = self.relevant_facts([t] + list(self.pc))
        if self.fp and self._has_fp([t] + list(self.pc)):
            try:
                rx = fprelax.Relaxer(fprelax.var_bounds(facts))
                F = [rx.conv(f) for f in facts] + [rx.conv(p) for p in self.pc] + [rx.conv(t)]
                q = z3.Solver()
                q.set("rlimit", RLIMIT)
                q.add(*F, *rx.side)
                r = q.check()
                if r == z3.unsat:
                    return False
                return True
            except fprelax.Unsupported:
                pass
        s = z3.Solver()
        s.set("rlimit", RLIMIT)
        s.add(*facts)
        s.add(*self.pc)
        s.add(t)
        return s.check() != z3.unsat

    def prove(self, goal, node, rule: str, msg: str, note: str | None = None):
        if self.bmc_mode:
            return
        if z3.is_true(z3.simplify(goal)):
            self._record(rule, node, msg, "simplifier", goal=goal)
            return
        facts = self.relevant_facts([goal] + list(self.pc))
        if self.fp and self._has_fp([goal] + list(self.pc)):
            for linear, how, budget in ((True, "smt-relaxed-linear", RLIMIT), (False, "smt-relaxed", RLIMIT)):
                try:
                    F, neg = fprelax.relaxed_query(facts, self.pc, goal, linear)
                except fprelax.Unsupported:
                    break
                q = z3.Solver()
                q.set("rlimit", budget)
                q.add(*F)
                q.add(neg)
                if q.check() == z3.unsat:
                    self._record(rule, node, msg, how, q)
                    return
        s = z3.Solver()
        s.set("rlimit", RLIMIT)
        s.add(*facts)
        s.add(*self.pc)
        s.add(z3.Not(goal))
        r = s.check()
        if r == z3.unsat:
            self._record(rule, node, msg, "smt", s)
            return
        if r == z3.sat:
            m = s.model()
            parts = []
            for v in get_vars(goal):
                name = str(v)
                desc = self.sym_desc.get(name)
                if desc is None or desc.startswith("_"):
                    continue
                val = m.eval(v, model_completion=True)
                parts.append(f"{desc} = {_fmt(val)}")
                if len(parts) >= 6:
                    break
            ce = "counterexample: " + (", ".join(parts) if parts else "(no free variables)")
            self.err(rule, msg, node, ce + (("\n" + note) if note else ""))
        ce = self._concrete_falsify(facts, goal) if self.fp else None
        if ce is not None:
            self.err(rule, msg, node, "counterexample (solver budget exceeded; the witness was confirmed by exact IEEE-754 "
                                      "evaluation): " + ce + (("\n" + note) if note else ""))
        self.err("R-602", "solver budget exceeded, not counted as a proof: " + msg, node,
                 "No result within the deterministic resource limit (R-806).")

    def _concrete_falsify(self, facts, goal, tries: int = 4000):
        import random
        forms = list(facts) + list(self.pc) + [z3.Not(goal)]
        vs = {}
        for f in forms:
            for v in get_vars(f):
                vs[str(v)] = v
        vs = [vs[k] for k in sorted(vs)]
        if not vs or len(vs) > 40:
            return None
        rng = random.Random(hash(str(goal)) & 0xFFFFFFFF)
        pool = [0.0, 1.0, -1.0, 0.5, -0.5, 0.25, -0.25, 2.0, -2.0, 0.1, -0.1]

        def cand(v):
            srt = v.sort()
            if srt.kind() == z3.Z3_FLOATING_POINT_SORT:
                x = rng.choice(pool) if rng.random() < 0.6 else rng.uniform(-4.0, 4.0)
                return z3.FPVal(x, srt)
            if srt == z3.IntSort():
                return z3.IntVal(rng.choice([0, 1, -1, 2, 255]) if rng.random() < 0.5 else rng.randint(-1000, 1000))
            if srt == z3.BoolSort():
                return z3.BoolVal(rng.random() < 0.5)
            if srt == z3.RealSort():
                return z3.RealVal(rng.choice(pool))
            return None
        for _ in range(tries):
            sub = []
            for v in vs:
                c = cand(v)
                if c is None:
                    return None
                sub.append((v, c))
            if all(z3.is_true(z3.simplify(z3.substitute(f, *sub))) for f in forms):
                parts = []
                for v, c in sub:
                    desc = self.sym_desc.get(str(v))
                    if desc and not desc.startswith("_") and v in set(get_vars(goal)):
                        parts.append(f"{desc} = {_fmt(c)}")
                return ", ".join(parts[:8]) or "(no free variables)"
        return None

    def resolve(self, t):
        if isinstance(t, A.TInt):
            return IntT(t.lo, t.hi)
        if isinstance(t, A.TFloat):
            if t.bits == 32 and t.lo is not None:
                return FloatT(t.unit, t.frame, self.f32(t.lo), self.f32(t.hi), 32)
            return FloatT(t.unit, t.frame, t.lo, t.hi, t.bits)
        if isinstance(t, A.TBool):
            return BoolT()
        if isinstance(t, A.TInstant):
            return InstantT(t.clock)
        if isinstance(t, A.TRes):
            if t.name not in self.resources:
                self.err("NAME", f"unknown resource '{t.name}' (no resource declaration)", t)
            decl = self.resources[t.name]["decl"]
            for st in t.states:
                if st not in decl.states:
                    self.err("R-203", f"resource '{t.name}' has no state '{st}' ({', '.join(decl.states)})", t)
            if len(set(t.states)) != len(t.states):
                self.err("R-203", f"{t.name}<...>: duplicate state", t)
            return ResT(t.name, frozenset(t.states))
        if isinstance(t, A.TBorrow):
            inner = self.resolve(t.inner)
            if not isinstance(inner, ResT) or inner.single() is None:
                self.err("R-203", "a borrow (&) applies only to a single-state resource type: &Resource<State>", t)
            return BorrowT(inner)
        if isinstance(t, A.TArray):
            ty = ArrayT(self.resolve(t.elem), t.n)
            if isinstance(ty.elem, (ResT, BorrowT)):
                self.err("R-203", "resources cannot be kept in arrays: each resource is a single named value", t)
            self.array_types.add(ty)
            return ty
        if isinstance(t, A.TSuspect):
            ty = SuspectT(self.resolve(t.inner))
            self.suspect_types.add(ty)
            return ty
        if isinstance(t, A.TBounded):
            ty = BoundedT(self.resolve(t.elem), t.n)
            self.bounded_types.add(ty)
            return ty
        if isinstance(t, A.TFix):
            return FixT(t.frac, t.unit, -(2 ** (t.total - 1)), 2 ** (t.total - 1) - 1)
        if isinstance(t, A.THeld):
            inner = self.resolve(t.inner)
            if not isinstance(inner, (IntT, FloatT, FixT, BoolT, EnumT)):
                self.err("R-403", f"Held can hold only a scalar value, {inner} given", t)
            return HeldT(inner)
        if isinstance(t, A.TLatch):
            return LatchT()
        if isinstance(t, A.TWire):
            if t.name not in self.encodings:
                self.err("NAME", f"unknown encoding '{t.name}'", t)
            return WireT(t.name)
        if isinstance(t, A.TName) and t.name in self.aliases:
            if t.name in self._alias_stack:
                self.err("NAME", f"cyclic type name: {' → '.join(self._alias_stack + [t.name])}", t)
            self._alias_stack.append(t.name)
            try:
                return self.resolve(self.aliases[t.name].type)
            finally:
                self._alias_stack.pop()
        if isinstance(t, A.TName):
            if t.name in self.enums:
                return EnumT(t.name)
            if t.name in self.structs:
                return RecordT(t.name)
            self.err("NAME", f"unknown type '{t.name}'", t)
        self.err("INTERNAL", f"unresolved type {t}", t)

    def check(self):
        items = self.prog.items
        for it in items:
            if isinstance(it, A.ModuleDecl):
                self.module = it
            elif isinstance(it, A.EnumDecl):
                if it.name in self.enums:
                    self.err("NAME", f"'{it.name}' defined twice", it)
                if len(set(it.variants)) != len(it.variants):
                    self.err("NAME", f"duplicate variant in '{it.name}'", it)
                self.enums[it.name] = it.variants
            elif isinstance(it, A.TypeAlias):
                if it.name in self.aliases or it.name in RESERVED:
                    self.err("NAME", f"type name '{it.name}' defined twice", it)
                self.aliases[it.name] = it
            elif isinstance(it, A.ScheduleDecl):
                self.schedules.append(it)
            elif isinstance(it, A.ResourceDecl):
                if it.name in self.resources or it.name in self.structs or it.name in self.enums:
                    self.err("NAME", f"'{it.name}' defined twice", it)
                self.resources[it.name] = {"decl": it}
            elif isinstance(it, A.TransitionDecl):
                key = (it.resource, it.src, it.dst)
                if key in self.transitions:
                    self.err("R-203", f"transition {it.resource}: {it.src} -> {it.dst} declared twice", it)
                self.transitions[key] = {"decl": it}
            elif isinstance(it, A.RotationDecl):
                self.check_rotation(it)
            elif isinstance(it, A.RegionDecl):
                if it.name in self.regions:
                    self.err("NAME", f"region '{it.name}' defined twice", it)
                self.regions[it.name] = {"decl": it}
            elif isinstance(it, A.ClockDecl):
                if self.clock_decl is not None:
                    self.err("R-307", "clock can be declared only once", it)
                self.clock_decl = it
            elif isinstance(it, A.WatchdogDecl):
                if it.name in self.watchdogs:
                    self.err("NAME", f"watchdog '{it.name}' defined twice", it)
                self.watchdogs[it.name] = it
            elif isinstance(it, A.ModeLattice):
                self.mode_edges += it.edges
                for src, dsts in it.edges:
                    if src != "any":
                        self.modes.add(src)
                    self.modes.update(dsts)
            elif isinstance(it, A.StructDecl):
                if it.name in self.structs or it.name in self.enums:
                    self.err("NAME", f"'{it.name}' defined twice", it)
                self.structs[it.name] = {"decl": it, "fields": [], "inv": it.invariant}
            elif isinstance(it, A.ProtectedDecl):
                if it.name in self.protected:
                    self.err("NAME", f"protected object '{it.name}' defined twice", it)
                self.protected[it.name] = {"decl": it, "fields": {}}
                for fn in it.fns:
                    qn = f"{it.name}.{fn.name}"
                    fn._prot, fn._op = it.name, fn.name
                    fn.name = qn
                    self.fns[qn] = fn
            elif isinstance(it, A.TaskDecl):
                if it.name in self.tasks:
                    self.err("NAME", f"task '{it.name}' defined twice", it)
                fn = A.FnDecl(f"{it.kind}:{it.name}", [], None, [], [], [], None, [], it.body,
                              line=it.line, col=it.col)
                fn._task = it.name
                fn._isr = it.kind == "isr"
                self.fns[fn.name] = fn
                self.tasks[it.name] = {"decl": it, "fn": fn, "kind": it.kind}
            elif isinstance(it, A.FnDecl):
                if it.name in self.fns:
                    self.err("NAME", f"function '{it.name}' defined twice", it)
                if it.name in RESERVED:
                    self.err("NAME", f"'{it.name}' is a built-in name and cannot be redefined", it,
                             "A name cannot mean two things; which one is called would not be visible at the call site (Principle B).")
                self.fns[it.name] = it
        for it in items:
            if isinstance(it, A.StructDecl):
                self.check_struct(it)
        self.check_resources()
        for it in items:
            if isinstance(it, A.FlightPhase):
                for m in it.modes:
                    if m not in self.modes:
                        self.err("05§5.5", f"unknown mode '{m}' in flight_phase", it)
                self.flight = set(it.modes)
        for it in items:
            if isinstance(it, A.ConstDecl):
                self.check_const(it)
            elif isinstance(it, A.ConfigDecl):
                self.check_config(it)
            elif isinstance(it, A.StateDecl):
                self.check_state(it)
            elif isinstance(it, A.EncodedDecl):
                self.check_encoded(it)
            elif isinstance(it, A.ProtectedDecl):
                for f in it.fields:
                    ty = self.resolve(f.type)
                    if isinstance(ty, (ResT, BorrowT)):
                        self.err("R-203", f"a protected object field cannot be a resource: '{it.name}.{f.name}'", f)
                    f._ty = ty
                    self.protected[it.name]["fields"][f.name] = {"ty": ty,
                                                                 "init": self.state_const(f.value, ty, f.name)}
            elif isinstance(it, A.TaskDecl):
                self.check_task_props(it)
            elif isinstance(it, A.FaultHypothesis):
                if it.name in self.hypotheses:
                    self.err("NAME", f"fault hypothesis '{it.name}' defined twice", it)
                self.hypotheses[it.name] = it
            elif isinstance(it, A.RedundancyDecl):
                if it.name in self.redundancy:
                    self.err("NAME", f"redundancy group '{it.name}' defined twice", it)
                self.redundancy[it.name] = it
        self.check_recursion()
        if self.regions:
            self.check_region_capacity()
        for name, fn in self.fns.items():
            self.sigs[name] = self.make_sig(fn)
        for name, fn in self.fns.items():
            if not fn.is_extern:
                self.check_fn(self.sigs[name])
        if self.resources:
            from .linear import check_linear
            check_linear(self)
        self.check_architecture()
        return self

    def call_closure(self, name: str) -> list:
        seen, order, stack = set(), [], [name]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            order.append(n)
            fn = self.fns[n]
            if fn.body is not None:
                for c in _walk(fn.body):
                    if isinstance(c, A.Call) and c.func in self.fns:
                        stack.append(c.func)
                    elif isinstance(c, A.ProtCall) and f"{c.obj}.{c.method}" in self.fns:
                        stack.append(f"{c.obj}.{c.method}")
        return order

    def call_path(self, src: str, dst: str) -> list:
        prev, queue = {src: None}, [src]
        while queue:
            n = queue.pop(0)
            if n == dst:
                path = []
                while n is not None:
                    path.append(n)
                    n = prev[n]
                return path[::-1]
            fn = self.fns[n]
            if fn.body is not None:
                for c in _walk(fn.body):
                    if isinstance(c, A.Call) and c.func in self.fns and c.func not in prev:
                        prev[c.func] = n
                        queue.append(c.func)
        return [src, dst]

    @staticmethod
    def io_of(fn: A.FnDecl) -> set:
        return {e.value for e in fn.effects if e.kind == "io"}

    def check_concurrency(self):
        for pname, pinfo in self.protected.items():
            for a in pinfo["decl"].annotations:
                if a.name == "ceiling":
                    self.err("R-302", f"protected object '{pname}': the ceiling priority cannot be written by hand", a,
                             "The ceiling is the highest priority of the tasks that use the object and is computed by "
                             "the compiler. A hand-written ceiling can be wrong (as in Ada) and lead to priority "
                             "inversion.\nMars Pathfinder (1997): priority inheritance was off on a mutex; "
                             "the high-priority task stayed blocked and the watchdog reset the system.")
        for name, fn in self.fns.items():
            if getattr(fn, "_prot", None) is None:
                continue
            for n in self.call_closure(name):
                body = self.fns[n].body
                for c in _walk(body):
                    if isinstance(c, A.ProtCall):
                        self.err("03§3.3", f"nested protected call: '{name}' → ... → '{c.obj}.{c.method}'", c,
                                 "A protected operation cannot call another protected object: there are no nested locks, "
                                 "so deadlock is impossible by construction.")
        touches: dict[str, set] = {}
        users: dict[str, set] = {p: set() for p in self.protected}
        for tname, tinfo in self.tasks.items():
            seen = set()
            for n in self.call_closure(tinfo["fn"].name):
                fn = self.fns[n]
                if getattr(fn, "_prot", None) is not None:
                    users[fn._prot].add(tname)
                    continue
                for c in _walk(fn.body):
                    if isinstance(c, (A.Name,)) and getattr(c, "_ref", None) == "state":
                        seen.add(c.name)
            for s in seen:
                touches.setdefault(s, set()).add(tname)
        for s, ts in touches.items():
            if len(ts) > 1:
                d = self.states[s]["decl"]
                self.err("03§3.3", f"data race: state '{s}' is accessed from more than one task: {', '.join(sorted(ts))}",
                         d, "State shared between tasks can live only inside a protected object.\n"
                            "Therac-25 (1985-87): a race between data entry and machine set-up led to lethal doses.")
        self.prot_users = users
        for p, ts in users.items():
            self.ceilings[p] = max((self.tasks[t]["priority"] for t in ts), default=0)
            isr = [t for t in ts if self.tasks[t].get("kind") == "isr"]
            self.notes.append(f"protected '{p}': ceiling priority {self.ceilings[p]} (computed by the compiler; "
                              f"tasks using it: {', '.join(sorted(ts)) or 'none'})"
                              + (f", accessed from interrupts ({', '.join(isr)}): ceiling in the interrupt range, R-306"
                                 if isr else ""))
        for tname, tinfo in self.tasks.items():
            if tinfo.get("kind") == "isr":
                self.notes.append(f"interrupt '{tname}': vector {tinfo['vector']}, level {tinfo['level']}, minimum "
                                  f"inter-arrival time {tinfo['period']*1e3:g} ms")
                continue
            self.notes.append(f"task '{tname}': period {tinfo['period']*1e3:g} ms, deadline "
                              f"{tinfo['deadline']*1e3:g} ms, priority {tinfo['priority']}")

    def op_wcet(self, fn: A.FnDecl):
        for e in fn.effects:
            if e.kind == "wcet":
                v, u = e.value
                return v * u.scale
        return None

    def _duration(self, e, node, what: str, rule: str) -> float:
        v, u = self.const_value(e, node)
        if u is None or not u.same_dimension(U.base("s")) or v <= 0:
            self.err(rule, f"{what} must be a positive duration (e.g. 10[ms])", node)
        return v * u.scale

    def clock_scenarios(self) -> list:
        if self.clock_decl is None:
            return [("", 1.0)]
        cd = self.clock_decl

        def freq(e):
            v, u = self.const_value(e, cd)
            if u is None or not u.same_dimension(U.base("Hz")) or v <= 0:
                self.err("R-307", "clock frequencies must be positive frequencies (e.g. 50[MHz])", cd)
            return v * u.scale
        f0 = freq(cd.nominal)
        out = [(f"nominal clock {f0 / 1e6:g} MHz", 1.0)]
        for e in cd.degraded:
            f = freq(e)
            if f >= f0:
                self.err("R-307", f"a degraded clock configuration ({f / 1e6:g} MHz) must be slower than the nominal ({f0 / 1e6:g} MHz) "
                                  "clock", cd)
            out.append((f"degraded clock {f / 1e6:g} MHz", f0 / f))
        return out

    def schedule_scenarios(self):
        base = {t: dict(i) for t, i in self.tasks.items()}
        self.mode_schedule = {}
        if not self.schedules:
            return [("", base, [])], []
        if not self.modes:
            self.err("R-305", "a schedule declaration needs a mode_lattice", self.schedules[0])
        isrs = {t: i for t, i in base.items() if i.get("kind") == "isr"}
        mode_of, names, scen, active_any = {}, set(), [], set()
        for sd in self.schedules:
            if sd.name in names:
                self.err("NAME", f"schedule '{sd.name}' defined twice", sd)
            names.add(sd.name)
            for m in sd.modes:
                if m not in self.modes:
                    self.err("R-305", f"schedule '{sd.name}': unknown mode '{m}'", sd)
                if m in mode_of:
                    self.err("R-305", f"mode '{m}' is bound to two schedules: '{mode_of[m]}' and '{sd.name}'", sd,
                             "A mode has exactly one schedule; which one applies cannot be left undecided.")
                mode_of[m] = sd.name
            act = {t: dict(i) for t, i in isrs.items()}
            for tn, props, line, col in sd.entries:
                where = A.Node(line=line, col=col)
                if tn not in self.tasks:
                    self.err("NAME", f"schedule '{sd.name}': unknown task '{tn}'", where)
                if self.tasks[tn].get("kind") == "isr":
                    self.err("R-305", f"schedule '{sd.name}': '{tn}' is an interrupt; interrupts are not bound to schedules, "
                                      "they are active in every mode", where)
                if tn in act:
                    self.err("R-305", f"schedule '{sd.name}': '{tn}' listed twice", where)
                ti = dict(base[tn])
                for k, e in props.items():
                    if k not in ("period", "wcet", "deadline"):
                        self.err("R-305", f"schedule '{sd.name}': unknown property '{k}' for '{tn}' "
                                          "(period, wcet, deadline; priority does not depend on the mode, ceilings are fixed)", where)
                    ti[k] = self._duration(e, where, f"'{tn}.{k}'", "R-305")
                if "deadline" not in props and "deadline" not in self.tasks[tn]["decl"].props:
                    ti["deadline"] = ti["period"]
                if ti["deadline"] > ti["period"] + 1e-15:
                    self.err("R-305", f"schedule '{sd.name}': deadline of '{tn}' is longer than its period", where)
                act[tn] = ti
                active_any.add(tn)
            scen.append((sd.name, act, list(sd.modes)))
        unbound = sorted(self.modes - set(mode_of))
        if unbound:
            self.err("R-305", f"mode(s) bound to no schedule: {', '.join(unbound)}", self.schedules[0],
                     "Every mode is bound to a schedule, and schedulability is proved for each schedule separately.\n"
                     "Safe-mode exercise (H-03): a single static schedule did not say what happens to the Payload "
                     "tasks in SafeHold.")
        idle = sorted(t for t, i in self.tasks.items() if i.get("kind") != "isr" and t not in active_any)
        if idle:
            self.err("R-305", f"task(s) active in no schedule: {', '.join(idle)}",
                     self.tasks[idle[0]]["decl"], "A task that runs in no mode is dead code (R-606, DO-178C §6.4.4.3).")
        trans = set()
        for src, dsts in self.mode_edges:
            for a in (sorted(self.modes) if src == "any" else [src]):
                for b in dsts:
                    if a in mode_of and b in mode_of and mode_of[a] != mode_of[b]:
                        trans.add((mode_of[a], mode_of[b]))
        self.mode_schedule = mode_of
        self.schedule_sets = {n: a for n, a, _ in scen}
        return scen, sorted(trans)

    def check_schedulability(self):
        if not self.tasks:
            if self.schedules or self.watchdogs:
                self.err("R-305", "a schedule/watchdog declaration needs tasks",
                         (self.schedules or list(self.watchdogs.values()))[0])
            return
        scen, trans = self.schedule_scenarios()
        missing = [t for t, i in self.tasks.items() if "wcet" not in i]
        if missing:
            if self.watchdogs:
                wd = next(iter(self.watchdogs.values()))
                self.err("R-307", f"the feeding interval of watchdog '{wd.name}' cannot be bounded: tasks {', '.join(missing)} "
                                  "declare no wcet", wd)
            self.notes.append(f"schedulability analysis not done: tasks {', '.join(missing)} declare no wcet "
                              f"(R-304)")
            return
        op_w = {}
        for name, fn in self.fns.items():
            if getattr(fn, "_prot", None) is not None:
                op_w.setdefault(fn._prot, []).append((fn._op, self.op_wcet(fn)))
        clocks = self.clock_scenarios()
        for clabel, scale in clocks:
            for sname, act, modes in scen:
                label = self._scen_label(sname, modes, clabel)
                self.timing[label] = self.rta(act, scale, label, op_w)
            for s1, s2 in trans:
                a1, a2 = self.schedule_sets[s1], self.schedule_sets[s2]
                union = {}
                for t in set(a1) | set(a2):
                    if t in a1 and t in a2:
                        p1, p2 = a1[t], a2[t]
                        u = dict(p1)
                        u["period"] = min(p1["period"], p2["period"])
                        u["deadline"] = min(p1["deadline"], p2["deadline"])
                        u["wcet"] = max(p1["wcet"], p2["wcet"])
                        union[t] = u
                    else:
                        union[t] = dict(a1.get(t) or a2[t])
                label = " · ".join(x for x in (f"transition '{s1}' → '{s2}'", clabel) if x)
                self.timing[label] = self.rta(union, scale, label, op_w, transition=(s1, s2))
        self.check_watchdogs(scen, trans, clocks)

    @staticmethod
    def _scen_label(sname, modes, clabel) -> str:
        return " · ".join(x for x in (f"schedule '{sname}' ({', '.join(modes)})" if sname else "", clabel) if x)

    def rta(self, act: dict, scale: float, label: str, op_w: dict, transition=None) -> dict:
        import math
        users = getattr(self, "prot_users", {})
        U_total = sum(i["wcet"] * scale / i["period"] for i in act.values())
        out = {}
        for tname, ti in sorted(act.items(), key=lambda kv: -kv[1]["priority"]):
            pi = ti["priority"]
            B, B_src = 0.0, None
            for obj, ts in users.items():
                if self.ceilings.get(obj, 0) < pi:
                    continue
                lower_ops = {self.fns[n]._op for t in ts if t in act and act[t]["priority"] < pi
                             for n in self.call_closure(self.tasks[t]["fn"].name)
                             if getattr(self.fns[n], "_prot", None) == obj}
                if lower_ops:
                    for op, w in op_w.get(obj, []):
                        if op not in lower_ops:
                            continue
                        if w is None:
                            self.err("R-304", f"no wcet declared for '{obj}.{op}': the blocking time of task '{tname}' "
                                              "cannot be bounded", self.tasks[tname]["decl"],
                                     "Under ICPP a task can wait on a protected object held by a lower-priority task for "
                                     "as long as the longest operation; without that time the response time is unknown.\n"
                                     "expected: fn op(...) effects { wcet <= 20[us] } { ... }")
                        if w * scale > B:
                            B, B_src = w * scale, f"{obj}.{op}"
            hp = [(t, i) for t, i in act.items() if t != tname and i["priority"] >= pi]
            C = ti["wcet"] * scale
            R = C + B
            for _ in range(10_000):
                Rn = C + B + sum(math.ceil(R / i["period"] - 1e-12) * i["wcet"] * scale for _, i in hp)
                if Rn == R or Rn > ti["deadline"]:
                    R = Rn
                    break
                R = Rn
            out[tname] = R
            if not label:
                self.tasks[tname]["response"] = R
            if R > ti["deadline"] + 1e-15:
                where = f" [{label}]" if label else ""
                why = ""
                if transition:
                    why = ("\nR-305: even if two schedules are each schedulable, during the transition window the old and new "
                           "task sets load the processor together. Fixes: defer the new tasks at the transition, split the load "
                           "into staged transitions, or add an intermediate mode.")
                elif scale > 1.0:
                    why = ("\nR-307: WCETs were declared at the nominal clock; in this configuration each takes "
                           f"{scale:g} times longer. Bootloader exercise (P-07): a signature check that fitted inside the watchdog at 50 MHz "
                           "triggered it on the 10 MHz backup oscillator, giving an endless boot loop.")
                self.err("R-305" if transition else ("R-307" if scale > 1.0 else "R-304"),
                         f"task '{tname}' is not schedulable{where}: worst-case response time ≥ {R*1e3:.4g} ms > deadline "
                         f"{ti['deadline']*1e3:.4g} ms", ti["decl"],
                         f"C = {C*1e3:.4g} ms, blocking B = {B*1e3:.4g} ms"
                         + (f" ({B_src}, ICPP)" if B_src else "")
                         + f", higher-priority tasks: {', '.join(t for t, _ in hp) or 'none'}; "
                           f"total utilisation U = {U_total:.3f}\n"
                           "Response-time analysis (Joseph & Pandya 1986): R = C + B + Σ ⌈R/Tⱼ⌉·Cⱼ.\n"
                           "Mars Pathfinder (1997): priority inversion made a task miss its deadline; "
                           "the watchdog reset the system again and again." + why)
            pre = f"[{label}] " if label else ""
            self.notes.append(f"timing {pre}'{tname}': R = {R*1e3:.4g} ms ≤ D = {ti['deadline']*1e3:.4g} ms "
                              f"(C {C*1e3:.4g}, B {B*1e3:.4g}{' ' + B_src if B_src else ''}; WCETs "
                              f"are declarations, not verified, R-301)")
        pre = f"[{label}] " if label else ""
        self.notes.append(f"{pre}total processor utilisation U = {U_total:.3f} (single core, fixed-priority preemptive)")
        return out

    def check_watchdogs(self, scen, trans, clocks):
        for wname, wd in self.watchdogs.items():
            for k in wd.props:
                if k not in ("timeout", "fed_by"):
                    self.err("R-307", f"watchdog '{wname}': unknown property '{k}' (timeout, fed_by)", wd)
            if "timeout" not in wd.props or "fed_by" not in wd.props:
                self.err("R-307", f"watchdog '{wname}': timeout and fed_by must be declared", wd)
            timeout = self._duration(wd.props["timeout"], wd, f"watchdog '{wname}' timeout", "R-307")
            fb = wd.props["fed_by"]
            if not isinstance(fb, A.Name) or fb.name not in self.tasks:
                self.err("R-307", f"watchdog '{wname}': fed_by must be a task name", wd)
            feeder = fb.name
            feeds = [n for n in self.call_closure(self.tasks[feeder]["fn"].name)
                     if self.fns[n].is_extern and wname in self.io_of(self.fns[n])]
            if not feeds:
                self.err("R-307", f"watchdog '{wname}': task '{feeder}' does not feed the watchdog", wd,
                         f"The feeding call is an extern function with the effect io({wname}); the declaration is checked "
                         f"against the code.\nexpected: extern fn wd_kick() effects {{ io({wname}) }};  and in task "
                         f"'{feeder}': wd_kick();")
            for clabel, scale in clocks:
                for sname, act, modes in scen:
                    label = self._scen_label(sname, modes, clabel)
                    if feeder not in act:
                        self.err("R-307", f"watchdog '{wname}': feeding task '{feeder}' is inactive in schedule '{sname}' "
                                          "", wd,
                                 f"In these modes ({', '.join(modes)}) the watchdog is not fed and the system resets.")
                    gap = act[feeder]["period"] + self.timing[label][feeder]
                    self._wd_gap(wname, wd, feeder, gap, timeout, label, scale)
                for s1, s2 in trans:
                    a1, a2 = self.schedule_sets[s1], self.schedule_sets[s2]
                    if feeder not in a1 or feeder not in a2:
                        continue
                    label = " · ".join(x for x in (f"transition '{s1}' → '{s2}'", clabel) if x)
                    gap = max(a1[feeder]["period"], a2[feeder]["period"]) + self.timing[label][feeder]
                    self._wd_gap(wname, wd, feeder, gap, timeout, label, scale)

    def _wd_gap(self, wname, wd, feeder, gap, timeout, label, scale):
        where = f" [{label}]" if label else ""
        if gap > timeout + 1e-15:
            self.err("R-307", f"watchdog '{wname}' can fire{where}: longest feeding interval "
                              f"{gap*1e3:.4g} ms > timeout {timeout*1e3:.4g} ms", wd,
                     f"Feeding task '{feeder}': interval = T + R (earliest start of one job to latest end of the next)."
                     + (f"\nIn this scenario the WCETs are {scale:g} times longer (degraded clock). Bootloader exercise (P-07): "
                        "the watchdog firing on the slow clock turned the recovery mechanism into an endless "
                        "boot loop." if scale > 1.0 else ""))
        self.notes.append(f"watchdog '{wname}'{where}: feeding interval ≤ {gap*1e3:.4g} ms ≤ {timeout*1e3:.4g} ms")

    def assurance_level(self) -> int:
        if self.module is None:
            return 1
        for a in self.module.annotations:
            if a.name == "assurance":
                if len(a.args) < 1 or not isinstance(a.args[0], A.Name) or a.args[0].name not in \
                        ("L0", "L1", "L2", "L3", "L4"):
                    self.err("06§6.2", "must be written as @assurance(L0 | L1 | L2 | L3 | L4)", a)
                return int(a.args[0].name[1])
        return 1

    def check_assurance(self):
        L = self.assurance_level()
        self.notes.append(f"assurance level: L{L}")
        if L >= 2 and self.float_model != "ieee":
            self.err("06§6.2", f"an L{L} module cannot be checked with the real-number floating-point model (--float-model ieee)",
                     self.module, "The real model 'proves' 0.1 + 0.2 ≤ 0.3; an L2+ claim must be about the arithmetic "
                                  "that actually runs (D-14).")
        if L >= 3:
            for name, fn in self.fns.items():
                anns = {a.name for a in fn.annotations}
                if "voter" in anns and "output_bound" not in anns:
                    self.err("R-605", f"selection/voting logic '{name}' in an L{L} module carries no output bound", fn,
                             "In L3+ modules, fault rejection or data selection logic must carry a property that bounds "
                             "the output deviation under a declared fault hypothesis.\n"
                             "expected: @output_bound(hypothesis = H, horizon = K, tolerance = …, max_step = …, "
                             "bound = …)\nQantas 72 (2008): the corner case was never asked about by any rule.")
            for tname, t in self.tasks.items():
                missing = [k for k in ("wcet", "stack") if k not in t]
                if missing:
                    self.err("R-304" if "wcet" in missing else "R-201",
                             f"task '{tname}' in an L{L} module does not declare {', '.join(missing)}", t["decl"],
                             "In L3+ modules, schedulability (R-304) and stack (R-201) analysis must be complete; "
                             "without the declaration the analysis would be skipped.")
        if L >= 4:
            self.err("06§6.2", "L4 cannot be reached with this toolchain", self.module,
                     "L4 needs machine-verified WCET and a verified-compilation certificate. spelfc does not verify "
                     "WCET (R-301; no hardware timing model) and the C compiler is not verified "
                     "(phase 4: CompCert / translation validation). The highest honest level is L3.")

    def check_architecture(self):
        self.check_assurance()
        self.check_concurrency()
        self.check_schedulability()
        for r in self.redundancy.values():
            self.check_redundancy(r)
        for name, fn in self.fns.items():
            for a in fn.annotations:
                if a.name == "safe_path":
                    self.check_safe_path(fn, a)
                elif a.name == "failover":
                    self.check_failover(fn, a)
                elif a.name == "voter":
                    self.check_voter(fn, a)
                elif a.name == "output_bound":
                    self.check_output_bound(fn, a)

    def state_writes(self, name: str) -> frozenset:
        if name in self._sw_cache:
            return self._sw_cache[name]
        out = set()
        for n in self.call_closure(name):
            fn = self.fns[n]
            if fn.body is not None:
                out |= _assigned_names(fn.body) & set(self.states)
        self._sw_cache[name] = frozenset(out)
        return self._sw_cache[name]

    REDUNDANCY_KEYS = ("channels", "hypothesis", "exchange", "rounds", "authenticated", "sync", "skew_us")

    def check_redundancy(self, r: A.RedundancyDecl):
        p = r.props
        for k in p:
            if k not in self.REDUNDANCY_KEYS:
                self.err("R-412", f"redundancy '{r.name}': unknown field '{k}' "
                                  f"(valid: {', '.join(self.REDUNDANCY_KEYS)})", r)
        for k in ("channels", "hypothesis", "exchange"):
            if k not in p:
                self.err("R-412", f"redundancy '{r.name}': '{k}' is required", r)
        n, hname, ex = p["channels"], p["hypothesis"], p["exchange"]
        rounds, auth = p.get("rounds", 0), p.get("authenticated", False)
        if not isinstance(n, int) or n < 2:
            self.err("R-412", f"redundancy '{r.name}': channels must be an integer ≥ 2", r)
        if hname not in self.hypotheses:
            self.err("R-412", f"redundancy '{r.name}': unknown fault hypothesis '{hname}'", r)
        h = self.hypotheses[hname]
        f, mode = h.channel_faults, h.fault_mode
        if f < 1 or mode is None:
            self.err("R-412", f"'{hname}' must declare channel_faults ≥ 1 and fault_mode for redundancy", h,
                     "An architecture that does not say which fault it is redundant against cannot be verified (Principle O).")
        if ex not in ("full_mesh", "consistent_broadcast", "none"):
            self.err("R-412", f"exchange must be full_mesh | consistent_broadcast | none, found '{ex}'", r)
        where = f"{n} channels, {hname}: f = {f}, mode = {mode}"
        if mode == "omission":
            need, why = f + 1, "with omission faults the output of every surviving channel is correct: n ≥ f + 1"
        elif mode == "value" or (mode == "byzantine" and ex == "consistent_broadcast"):
            need, why = 2 * f + 1, "masking a wrong value by voting needs a healthy majority: n ≥ 2f + 1"
        elif mode == "byzantine" and ex == "none":
            self.err("R-412", f"'{r.name}': Byzantine fault hypothesis but no cross-channel exchange ({where})", r,
                     "Channels cannot reach the same decision without knowing what the others saw; each channel votes "
                     "on different inputs.\nexpected: exchange = full_mesh, rounds ≥ f + 1")
        elif auth:
            need, why = 2 * f + 1, ("with signed messages (SM(m)) agreement is possible for any n, but masking the faulty channel's "
                                    "output by voting needs a majority: n ≥ 2f + 1")
        else:
            need, why = 3 * f + 1, ("with unsigned messages (OM(m)) f Byzantine faults need 3f + 1 nodes "
                                    "(Lamport, Shostak, Pease 1982)")
        if n < need:
            scen = ""
            if mode == "byzantine" and not auth and ex == "full_mesh":
                scen = (f"\nconcrete scenario: faulty channel C{n} sends 10 to C1 and 20 to C2, then in the exchange "
                        f"round relays to each the opposite of what the other 'said'.\n"
                        f"C1 cannot tell 'C2 is lying' from 'C{n} is lying'; the two healthy channels "
                        f"agree on different values and the voters produce different commands.\n"
                        f"Driscoll et al. (2003): real Byzantine faults have been documented in aviation.")
            self.err("R-412", f"redundancy architecture '{r.name}' cannot tolerate the fault hypothesis: "
                              f"{n} channels, at least {need} needed ({where})", r,
                     why + scen + "\nexpected: add channels or use authenticated cross-channel exchange "
                                  "(authenticated = true); both have a real hardware and time cost.")
        if mode == "byzantine" and ex == "full_mesh" and rounds < f + 1:
            self.err("R-412", f"'{r.name}': {f} Byzantine faults need at least {f + 1} exchange rounds, "
                              f"{rounds} declared", r,
                     "Dolev-Strong lower bound: deterministic Byzantine agreement needs f + 1 rounds.")
        mech = ("consistent broadcast assumed (a hardware claim; Driscoll 2003: SOS faults can break this assumption)"
                if ex == "consistent_broadcast" else f"{ex}, {rounds} rounds, "
                + ("signed" if auth else "unsigned"))
        self.notes.append(f"redundancy '{r.name}': {where} → required n ≥ {need}, have {n} ✓ ({mech})")

    def check_voter(self, fn: A.FnDecl, a: A.Annotation):
        pos = [x for x in a.args if not isinstance(x, tuple)]
        kw = {k: v for k, v in (x for x in a.args if isinstance(x, tuple))}
        if len(pos) != 1 or not isinstance(pos[0], A.Name) or pos[0].name not in self.redundancy:
            self.err("R-412", "must be written as @voter(REDUNDANCY_GROUP [, tolerance = …, signal_rate = …])", a)
        a = A.Annotation(a.name, pos, line=a.line, col=a.col) if kw else a
        self.check_vote_tolerance(fn, self.redundancy[pos[0].name], kw, a)
        r = self.redundancy[a.args[0].name]
        n = r.props["channels"]
        sig = self.sigs[fn.name]
        if len(sig.params) != n:
            self.err("R-412", f"voter '{fn.name}' reads {len(sig.params)} channels, '{r.name}' declares {n} channels",
                     a, "The architecture declaration and the code must see the same number of channels; otherwise the "
                        "proof is about another system.")
        tys = {str(t) for _, t, _ in sig.params}
        if len(tys) != 1:
            self.err("R-412", f"voter '{fn.name}': all channels must have the same type ({', '.join(sorted(tys))})", a)

    def check_vote_tolerance(self, fn, r: A.RedundancyDecl, kw: dict, a):
        if "tolerance" not in kw:
            return
        tol, tu = self.const_value(kw["tolerance"], a)
        sync = r.props.get("sync")
        if sync == "frame":
            self.notes.append(f"voter '{fn.name}': tolerance {tol:g}{'[' + str(tu) + ']' if tu else ''}; channels "
                              f"frame-synchronised (ASSUMPTION: synchronisation is the substrate's contract, R-413)")
            return
        skew = r.props.get("skew_us")
        if skew is None or "signal_rate" not in kw:
            self.err("R-413", f"voter '{fn.name}': fixed tolerance without justification", a,
                     "On loosely synchronised channels even three healthy channels differ by skew × rate of change. "
                     "A fixed tolerance either isolates a healthy channel or misses a real fault.\n"
                     "expected: redundancy G { …, skew_us = N } and @voter(G, tolerance = …, signal_rate = …), "
                     "or redundancy G { …, sync = frame }")
        rate, ru = self.const_value(kw["signal_rate"], a)
        if tu is None or ru is None or not (ru * U.base("s")).same_dimension(tu):
            self.err("R-413", f"voter '{fn.name}': the unit of signal_rate must be the tolerance unit / s", a)
        need = rate * ru.scale * (skew * 1e-6) / tu.scale
        if tol < need:
            self.err("R-413", f"voter '{fn.name}': tolerance {tol:g} [{tu}] < skew × rate of change = {need:.4g} [{tu}]",
                     a, f"The channels can be {skew} µs apart; while the signal changes at {rate:g} [{ru}] the healthy channels "
                        f"diverge by {need:.4g} [{tu}] and the voter declares a healthy channel faulty.\n"
                        "The tolerance must be ≥ skew × rate of change (K-04, W-09).")
        self.notes.append(f"voter '{fn.name}': tolerance {tol:g} [{tu}] ≥ skew {skew} µs × rate {rate:g} [{ru}] "
                          f"= {need:.4g} [{tu}] (R-413)")

    def _bound_const(self, kw, key, unit, a):
        if key not in kw:
            self.err("R-605", f"@output_bound: '{key}' is required", a)
        val, u = self.const_value(kw[key], a)
        if isinstance(unit, U.Unit):
            if u is None or not u.same_dimension(unit):
                self.err("R-605", f"@output_bound: '{key}' must have the dimension of {unit}", a)
            val = val * u.scale / unit.scale
        if val < 0:
            self.err("R-605", f"@output_bound: '{key}' cannot be negative", a)
        return val

    def check_output_bound(self, fn: A.FnDecl, a: A.Annotation):
        import itertools
        kw = {k: v for k, v in (x for x in a.args if isinstance(x, tuple))}
        hv = kw.get("hypothesis")
        if not isinstance(hv, A.Name) or hv.name not in self.hypotheses:
            self.err("R-605", "@output_bound(hypothesis = HYPOTHESIS, ...) needs a known fault hypothesis", a)
        h = self.hypotheses[hv.name]
        sig = self.sigs[fn.name]
        params = sig.params
        n, f = len(params), h.channel_faults
        if n < 2:
            self.err("R-605", f"'{fn.name}': @output_bound needs at least two source parameters", a)
        ptys = {str(t) for _, t, _ in params}
        pty = params[0][1]
        if len(ptys) != 1 or not isinstance(pty, (FloatT, IntT)):
            self.err("R-605", f"'{fn.name}': source parameters must have the same numeric type ({', '.join(sorted(ptys))})", a)
        if isinstance(pty, FloatT) and pty.lo is None:
            self.err("R-605", f"'{fn.name}': the source type must carry a range (the space of arbitrary values must be finite)", a)
        rty = sig.ret
        if type(rty) is not type(pty) or (isinstance(pty, FloatT) and not rty.unit.same_unit(pty.unit)):
            self.err("R-605", f"'{fn.name}': the output type must have the same unit as the sources ({rty} / {pty})", a)
        if not (1 <= f < n):
            self.err("R-605", f"'{hv.name}': requires 1 ≤ channel_faults < number of sources ({n})", a)
        if any(isinstance(x, A.Halt) for x in _walk(fn.body)):
            self.err("R-605", f"'{fn.name}': the output of a selection logic that contains halt cannot be bounded", a)
        hz = kw.get("horizon")
        if not isinstance(hz, A.IntLit) or not (1 <= hz.value <= 64):
            self.err("R-605", "@output_bound: horizon must be an integer in 1..=64", a)
        K = hz.value
        unit = pty.unit if isinstance(pty, FloatT) else None
        tol = self._bound_const(kw, "tolerance", unit, a)
        step = self._bound_const(kw, "max_step", unit, a)
        bound = self._bound_const(kw, "bound", unit, a)
        uname = f" {unit}" if unit is not None else ""

        def lit(x):
            return self.fval(x, pty.bits) if isinstance(pty, FloatT) else z3.IntVal(int(x))

        def init_term(info):
            ty, v = info["ty"], info.get("init")
            if info["kind"] != "scalar" or v is None:
                return None
            if isinstance(ty, BoolT):
                return z3.BoolVal(bool(v))
            if isinstance(ty, FloatT):
                return self.fval(v, ty.bits)
            if isinstance(ty, (IntT, EnumT, FixT)):
                return z3.IntVal(int(v))
            return None

        saved = (self.facts, self.pc, self.cur, self.bmc_mode, self.bmc_returns, self.pc_taint)
        saved_fp = (self.fp, getattr(self, "relaxed", False))
        if self.fp:
            self.fp, self.relaxed = False, True
        pnames = [p for p, _, _ in params]
        def unroll_step(k, st, truth_prev, faulty, healthy_start):
            t_k = self.sym(pty, f"_true@{k}")
            if truth_prev is not None:
                self.facts += [t_k - truth_prev <= lit(step), truth_prev - t_k <= lit(step)]
            xs = [self.sym(pty, f"_{p}@{k}") for p in pnames]
            for i, x in enumerate(xs):
                if i not in faulty or k < healthy_start:
                    self.facts += [x - t_k <= lit(tol), t_k - x <= lit(tol)]
            env = {p: Binding(pty, x, False, "param") for p, x in zip(pnames, xs)}
            for s, info in self.states.items():
                term = st[s] if st[s] is not None else self.sym(info["ty"], f"_{s}@{k}")
                env[s] = Binding(info["ty"], term, True, "state")
            self.pc, self.pc_taint, self.bmc_returns = [], False, []
            env2, term, tail = self.exec_block(fn.body, env)
            exits = list(self.bmc_returns)
            if not term and tail is not None:
                v2 = self.coerce(tail, sig.ret, fn.body, "return value")
                exits.append((z3.And(*self.pc) if self.pc else z3.BoolVal(True), v2.term,
                              {s: env2[s].term for s in self.states}))
            if not exits:
                self.err("R-605", f"'{fn.name}' returns no value on any path", fn)
            out = exits[-1][1]
            nst = dict(exits[-1][2])
            for cond, val, sts in reversed(exits[:-1]):
                out = z3.If(cond, val, out)
                for s in nst:
                    nst[s] = _ite(cond, sts[s], nst[s])
            st = {s: (nst[s] if isinstance(nst.get(s), z3.ExprRef) else None) for s in self.states}
            o_k = self.sym(rty, f"_out@{k}")
            self.facts.append(o_k == out)
            return xs, t_k, o_k, st

        try:
            for faulty in itertools.combinations(range(n), f):
                self.facts, self.bmc_mode, self.cur = [], True, sig
                st = {s: init_term(info) for s, info in self.states.items()}
                truth_prev, trace, viol = None, [], []
                for k in range(K):
                    xs, t_k, o_k, st = unroll_step(k, st, truth_prev, faulty, h.healthy_start)
                    viol.append(z3.Or(o_k - t_k > lit(bound), t_k - o_k > lit(bound)))
                    trace.append((xs, t_k, o_k))
                    truth_prev = t_k
                margin = self.fval(max(1.0, bound) * 1e-9) if isinstance(pty, FloatT) else z3.IntVal(0)
                s = z3.Solver()
                s.set("rlimit", RLIMIT * 4)
                s.add(*self.facts)
                s.add(z3.Or(*[z3.Or(o - t > lit(bound) + margin, t - o > lit(bound) + margin)
                              for (_, t, o) in trace]))
                r = s.check()
                marginal = False
                if r == z3.unsat and isinstance(pty, FloatT):
                    s = z3.Solver()
                    s.set("rlimit", RLIMIT * 4)
                    s.add(*self.facts)
                    s.add(z3.Or(*viol))
                    r = s.check()
                    marginal = r == z3.sat
                if r == z3.unsat:
                    self._record("R-605", a, f"output bound of '{fn.name}', {hv.name}, faulty sources "
                                             f"{[pnames[i] for i in faulty]}, horizon {K}", "smt", s)
                if r == z3.unknown:
                    self.err("R-602", f"'{fn.name}': bounded-horizon model checking exceeded the budget, not counted as a proof", a,
                             "No result within the deterministic resource limit (R-806); reduce the horizon.")
                if r == z3.sat:
                    m = s.model()

                    def val(x):
                        return _fmt(m.eval(x, model_completion=True))

                    def num(x):
                        return _const_of(m.eval(x, model_completion=True))

                    bad = next(k for k in range(K) if z3.is_true(m.eval(viol[k], model_completion=True)))
                    fnames = ", ".join(pnames[i] for i in faulty)
                    w = max(13, *(len(p) + 1 for p in pnames))
                    head = "    step " + "".join(f"{p:>{w}}" for p in pnames) + f"{'true':>{w}}{'output':>{w}}"
                    rows = [head]
                    for k, (xs, t_k, o_k) in enumerate(trace[:bad + 1]):
                        mark = "  ← bound exceeded" if k == bad else ""
                        cells = "".join(f"{val(x) + ('*' if i in faulty and k >= h.healthy_start else ''):>{w}}"
                                        for i, x in enumerate(xs))
                        rows.append(f"    {k:>4} " + cells + f"{val(t_k):>{w}}{val(o_k):>{w}}" + mark)
                    dev = abs(num(trace[bad][2]) - num(trace[bad][1]))
                    if marginal:
                        self.err("R-605", f"output bound of '{fn.name}' not proved under {hv.name}: only "
                                          f"a rounding-level violation (≤ 1e-9 relative) was found; the bound is right at the edge", a,
                                 "The claimed bound can be exceeded once IEEE-754 rounding is taken into account. Relax "
                                 "the bound by a small margin or fix the edge behaviour of the algorithm.")
                    self.err("R-605", f"the output of '{fn.name}' exceeds its bound under {hv.name}: at step {bad} "
                                      f"|output − true| = {dev:.6g}{uname} > {bound:g}{uname}", a,
                             f"faulty source: {fnames} (arbitrary value, marked with *; step ≥ {h.healthy_start})\n"
                             f"assumption: healthy sources within ±{tol:g}{uname} of the truth, the true signal changes by "
                             f"≤ {step:g}{uname} per step\n"
                             + "\n".join(rows) + "\n"
                             "Making parameters visible does not make corner cases visible. Qantas 72 (2008, ATSB "
                             "AO-2008-070): AOA spikes arrived at intervals equal to the hold time; the second spike "
                             "passed the filter and the aircraft was commanded nose-down twice.")
            unb = kw.get("unbounded")
            if isinstance(unb, A.BoolLit) and unb.value:
                for faulty in itertools.combinations(range(n), f):
                    self.facts, self.bmc_mode, self.cur = [], True, sig
                    st = {s_: (self.sym(info["ty"], f"_{s_}@window") if init_term(info) is not None else None)
                          for s_, info in self.states.items()}
                    truth_prev, last = None, None
                    for k in range(K + 1):
                        xs, t_k, o_k, st = unroll_step(k, st, truth_prev, faulty, 0)
                        good = z3.And(o_k - t_k <= lit(bound), t_k - o_k <= lit(bound))
                        if k < K:
                            self.facts.append(good)
                        else:
                            last = z3.Not(good)
                        truth_prev = t_k
                    q = z3.Solver()
                    q.set("rlimit", RLIMIT * 4)
                    q.add(*self.facts)
                    q.add(last)
                    rq = q.check()
                    if rq != z3.unsat:
                        self.err("R-605", f"'{fn.name}': unbounded-horizon claim not proved, the property is not {K}-inductive "
                                          f"(faulty source {[pnames[i] for i in faulty]})", a,
                                 "A window that starts from an arbitrary state and keeps the property for K steps can "
                                 "break it at the next step. Either enlarge the horizon, add an invariant that bounds "
                                 "the algorithm's state (e.g. isolate the permanently faulty source, D-10), or keep the claim "
                                 "to a bounded horizon (remove unbounded).")
                    self._record("R-605", a, f"k-induction step of '{fn.name}' (k = {K}), faulty source "
                                             f"{[pnames[i] for i in faulty]}", "smt", q)
                self.notes.append(f"output bound '{fn.name}': under {hv.name} (f = {f}/{n} sources arbitrary) "
                                  f"|output − true| ≤ {bound:g}{uname} proved for ALL time "
                                  f"(k-induction, k = {K}; R-605)")
            else:
                self.notes.append(f"output bound '{fn.name}': under {hv.name} (f = {f}/{n} sources arbitrary) "
                                  f"|output − true| ≤ {bound:g}{uname} proved over a horizon of {K} steps "
                                  f"(bounded horizon, no claim beyond it, R-605)")
        finally:
            self.facts, self.pc, self.cur, self.bmc_mode, self.bmc_returns, self.pc_taint = saved
            self.fp, self.relaxed = saved_fp

    def check_safe_path(self, fn: A.FnDecl, a: A.Annotation):
        if len(a.args) != 1 or not isinstance(a.args[0], A.Name):
            self.err("R-410", "must be written as @safe_path(FAULT_HYPOTHESIS)", a)
        hname = a.args[0].name
        if hname not in self.hypotheses:
            self.err("R-410", f"unknown fault hypothesis '{hname}'", a)
        failed = set(self.hypotheses[hname].failed)
        for n in self.call_closure(fn.name):
            bad = self.io_of(self.fns[n]) & failed
            if bad:
                path = " → ".join(self.call_path(fn.name, n))
                dev = sorted(bad)[0]
                self.err("R-410", f"safe-mode path '{fn.name}' depends on device '{dev}', which hypothesis {hname} "
                                  f"declares FAILED", a,
                         f"call path: {path}  [io({dev})]\n"
                         "A safe mode that cannot be reached when the fault occurs is not a safe mode; it creates an "
                         "illusion of assurance.\nReachability is proved under the declared fault hypothesis, not in the "
                         "nominal case (Principle O).")
        self.notes.append(f"safe path '{fn.name}' verified under {hname} "
                          f"({len(self.call_closure(fn.name))} functions, failed devices: {sorted(failed)})")

    DIVERSITY_DIMS = ("hardware", "compiler", "source", "requirements", "team")

    def check_failover(self, fn: A.FnDecl, a: A.Annotation):
        backup = None
        for arg in a.args:
            if isinstance(arg, tuple) and arg[0] == "backup" and isinstance(arg[1], A.Name):
                backup = arg[1].name
        if backup is None or backup not in self.fns:
            self.err("R-405", "must be written as @failover(backup = function)", a)
        p_set = set(self.call_closure(fn.name)) - {fn.name}
        b_set = set(self.call_closure(backup)) - {backup}
        shared = sorted(p_set & b_set)
        div = next((x for x in fn.annotations if x.name == "diverse"), None)
        if div is None:
            if shared:
                self.err("R-405", f"backup '{backup}' shares code with primary '{fn.name}': {', '.join(shared)}",
                         a, "The same code fails the same way on the same input; two identical channels are one channel.\n"
                            "Ariane 5 Flight 501: SRI2 and SRI1 failed the same way, with the same code, 72 ms apart.\n"
                            "Either diversify the code or write a five-dimensional justification with @diverse(...) (R-411).")
            return
        given = {}
        for arg in div.args:
            if not isinstance(arg, tuple):
                self.err("R-411", "@diverse arguments must have the form dimension = value", div)
            k, v = arg
            if k not in self.DIVERSITY_DIMS:
                self.err("R-411", f"unknown diversity dimension '{k}' (valid: {', '.join(self.DIVERSITY_DIMS)})", div)
            given[k] = v
        missing = [d for d in self.DIVERSITY_DIMS if d not in given]
        if missing:
            self.err("R-411", f"diversity vector incomplete: {', '.join(missing)}", div,
                     "Each dimension protects against a different fault class; each dimension left in common needs its own justification.")
        for k, v in given.items():
            if isinstance(v, A.Name) and v.name == "diverse":
                if k == "source" and shared:
                    self.err("R-411", f"the claim 'source = diverse' is FALSE: code is shared: {', '.join(shared)}", div,
                             "Source diversity is checked against the call graph; the claim contradicts the evidence.")
                continue
            if isinstance(v, A.Call) and v.func in ("common", "partial"):
                why = v.kwargs.get("because") if v.func == "common" else (v.args[0] if v.args else None)
                if not isinstance(why, A.StrLit) or not why.value.strip():
                    self.err("R-411", f"'{k}' common/partial: a justification is required: {v.func}(because = \"...\")", div)
                continue
            self.err("R-411", f"'{k}': the value must be diverse, common(because = \"...\") or partial(\"...\")", div)
        self.notes.append(f"redundancy '{fn.name}' → '{backup}': shared code {shared or 'none'}; diversity vector justified")

    def const_value(self, e, node):
        if isinstance(e, A.IntLit):
            return e.value, None
        if isinstance(e, A.FloatLit):
            return e.value, e.unit
        if isinstance(e, A.Unary) and e.op == "-":
            v, u = self.const_value(e.operand, node)
            return -v, u
        if isinstance(e, A.Binary) and e.op in "+-*/":
            a, ua = self.const_value(e.left, node)
            b, ub = self.const_value(e.right, node)
            if e.op == "+":
                return a + b, ua or ub
            if e.op == "-":
                return a - b, ua or ub
            if e.op == "*":
                return a * b, ua or ub
            if b == 0:
                self.err("R-501", "division by zero in a constant expression", e)
            return (a // b if isinstance(a, int) and isinstance(b, int) else a / b), ua or ub
        if isinstance(e, A.Name) and e.name in self.consts:
            v = self.consts[e.name]
            return _const_of(v.term), None
        self.err("05§5.7", "constant and configuration values must be computable at compile time", e)

    def check_const_value(self, ty, value, unit, node, where: str, rule: str):
        if isinstance(ty, IntT):
            if not isinstance(value, int):
                self.err(rule, f"{where}: expected an integer, found {value}", node)
            if not (ty.lo <= value <= ty.hi):
                self.err(rule, f"{where}: value {value} is outside the range of {ty}", node,
                         "Configuration data is typed like code (05 §5.7). "
                         "Titan IVB/Milstar-3 (1999) was lost to a decimal-point error.")
            return z3.IntVal(value)
        if isinstance(ty, FloatT):
            if unit is not None and not unit.is_dimensionless() and not unit.same_unit(ty.unit):
                self.err("05§5.2", f"{where}: unit mismatch, {unit} given, {ty.unit} expected", node)
            if unit is None or unit.is_dimensionless():
                if not ty.unit.is_dimensionless():
                    self.err("05§5.2", f"{where}: value without a unit; the unit {ty.unit} must be written "
                                       f"(e.g. {value}[{ty.unit}])", node)
            if ty.lo is not None and not (ty.lo <= value <= ty.hi):
                self.err(rule, f"{where}: value {value} is outside the range [{ty.lo}, {ty.hi}]", node,
                         "Configuration data is typed like code (05 §5.7). "
                         "Titan IVB/Milstar-3 (1999) was lost to a decimal-point error: "
                         "-1.992476 instead of -0.1992476.")
            return self.fval(value, ty.bits)
        self.err(rule, f"{where}: in Core v0 constants must be Int or Float", node)

    def check_const(self, c: A.ConstDecl):
        ty = self.resolve(c.type)
        v, u = self.const_value(c.value, c)
        term = self.check_const_value(ty, v, u, c.value, f"constant '{c.name}'", "R-501")
        self.consts[c.name] = Val(ty, term)
        self.const_decls[c.name] = c
        c._ty = ty
        c._cval = v

    def check_config(self, c: A.ConfigDecl):
        fields = {}
        for f in c.fields:
            ty = self.resolve(f.type)
            v, u = self.const_value(f.value, f)
            term = self.check_const_value(ty, v, u, f.value, f"configuration '{c.name}.{f.name}'", "05§5.7")
            fields[f.name] = (ty, term, v)
            f._ty = ty
            f._cval = v
        self.configs[c.name] = fields

    def check_struct(self, d: A.StructDecl):
        seen, fields = set(), []
        for fname, ftype in d.fields:
            if fname in seen:
                self.err("NAME", f"field '{fname}' appears twice in '{d.name}'", d)
            seen.add(fname)
            fty = self.resolve(ftype)
            if isinstance(fty, (HeldT, LatchT, BoundedT, SuspectT)):
                self.err("TYPE", f"'{d.name}.{fname}': {fty} cannot be a record field", d)
            if fty == RecordT(d.name):
                self.err("TYPE", f"'{d.name}' cannot contain itself", d)
            if isinstance(fty, (ResT, BorrowT)):
                self.err("R-203", f"'{d.name}.{fname}': a resource cannot be a record field (records are copied)", d)
            fields.append((fname, fty))
        self.structs[d.name]["fields"] = fields
        if d.invariant is not None:
            env = {f: Binding(fty, self.sym(fty, f"_{d.name}.{f}"), False, "param") for f, fty in fields}
            v = self.eval(d.invariant, env)
            self.expect_bool(v, d.invariant, "record invariant")

    def eval_struct_lit(self, e: A.StructLit, env):
        if e.name not in self.structs:
            self.err("NAME", f"unknown record type '{e.name}'", e)
        info = self.structs[e.name]
        want = [f for f, _ in info["fields"]]
        got = [f for f, _ in e.fields]
        if sorted(got) != sorted(want) or len(set(got)) != len(got):
            missing = sorted(set(want) - set(got))
            extra = sorted(set(got) - set(want))
            self.err("00§0.2-E", f"value of '{e.name}': every field must be given exactly once"
                                 + (f"; missing: {missing}" if missing else "") + (f"; extra: {extra}" if extra else ""), e)
        ftys = dict(info["fields"])
        val, taint = {}, False
        for fname, fexpr in e.fields:
            v = self.eval(fexpr, env, ftys[fname])
            self.no_value_check(v, fexpr)
            v = self.coerce(v, ftys[fname], fexpr, f"'{e.name}.{fname}'")
            val[fname] = v.term
            taint = taint or v.taint
        if info["inv"] is not None:
            ienv = {f: Binding(fty, val[f], False, "param") for f, fty in info["fields"]}
            inv = self.eval(info["inv"], ienv)
            self.prove(inv.term, e, "R-505", f"the representation invariant of '{e.name}' could not be proved for this value",
                       "Types that can represent the same abstract value in more than one way carry a canonical form.\n"
                       "Quaternions are a double cover: q and −q are the same rotation, but the sign of the ERROR quaternion "
                       "decides whether the vehicle turns the short way (1°) or the long way (359°).\n"
                       "expected: if w < 0, negate all components (canonicalise).")
        return Val(RecordT(e.name), val, taint)

    RES_FIELD_TYPES = (IntT, FixT, FloatT, BoolT, EnumT, InstantT)

    def check_resources(self):
        for rname, reg in self.regions.items():
            d = reg["decl"]
            for k in d.props:
                if k not in ("size", "kind", "line"):
                    self.err("R-209", f"region '{rname}': unknown property '{k}' (size, kind, line)", d)
            if "size" not in d.props:
                self.err("R-209", f"region '{rname}': size must be declared (e.g. size = 4[KiB])", d)
            v, u = self.const_value(d.props["size"], d)
            if u is not None:
                if not u.same_dimension(U.base("B")):
                    self.err("R-209", f"region '{rname}': size must be a memory size (B, KiB, MiB)", d)
                v = v * u.scale
            if v != int(v) or v <= 0:
                self.err("R-209", f"region '{rname}': size must be a positive number of bytes", d)
            kind = "ram"
            if "kind" in d.props:
                kn = d.props["kind"]
                if not isinstance(kn, A.Name) or kn.name not in ("ram", "dma", "nvm"):
                    self.err("R-209", f"region '{rname}': kind must be ram | dma | nvm", d)
                kind = kn.name
            line = 32
            if "line" in d.props:
                line, _ = self.const_value(d.props["line"], d)
                if not isinstance(line, int) or line <= 0 or line & (line - 1):
                    self.err("R-209", f"region '{rname}': line (cache line) must be a positive power of two", d)
            reg.update(size=int(v), kind=kind, line=line)
        from .layout import struct_size
        for name, info in self.resources.items():
            d = info["decl"]
            if len(set(d.states)) != len(d.states):
                self.err("R-203", f"resource '{name}': duplicate state", d)
            for st in d.device:
                if st not in d.states:
                    self.err("R-205", f"resource '{name}': unknown state '{st}' in the device set", d)
            if d.states[0] in d.device:
                self.err("R-205", f"resource '{name}': the initial state ('{d.states[0]}') cannot be a device state", d,
                         "Static storage belongs to the CPU at start-up; handing it to a device takes an explicit transition.")
            cpu = [st for st in d.states if st not in d.device]
            read = d.read if d.read is not None else cpu
            write = d.write if d.write is not None else cpu
            for st in list(read) + list(write):
                if st not in d.states:
                    self.err("R-203", f"resource '{name}': unknown state '{st}' in the access set", d)
                if st in d.device:
                    self.err("R-205", f"resource '{name}': '{st}' is a device state; CPU access cannot be defined", d,
                             "The CPU touching the buffer while a transfer is in progress is a race (J-01).")
            fields, seen = [], set()
            for fname, ftype in d.fields:
                if fname in seen:
                    self.err("NAME", f"resource '{name}': field '{fname}' appears twice", d)
                seen.add(fname)
                fty = self.resolve(ftype)
                base = fty.elem if isinstance(fty, ArrayT) else fty
                if not isinstance(base, self.RES_FIELD_TYPES):
                    self.err("R-203", f"resource '{name}.{fname}': {fty} cannot be a resource field "
                                      "(integer, fixed point, float, bool, enum and arrays of these)", d)
                fields.append((fname, fty))
            if d.region is not None and d.region not in self.regions:
                self.err("NAME", f"resource '{name}': unknown region '{d.region}'", d)
            if d.device and (d.region is None or self.regions[d.region]["kind"] != "dma"):
                self.err("R-205", f"resource '{name}' can be handed to a device but is not in a region of kind 'dma'", d,
                         "A DMA buffer must be aligned to a cache line and a multiple of the line size; otherwise invalidating "
                         "a line also wipes the neighbouring variable. The compiler provides this alignment only in a dma region.\n"
                         "expected: region dma_ram { size = 4[KiB], kind = dma }  and  resource … in dma_ram …")
            info.update(states=list(d.states), device=set(d.device), read=set(read), write=set(write),
                        fields=fields, size=struct_size(fields) if fields else 0, region=d.region)
        for (rn, a, b), t in self.transitions.items():
            d = t["decl"]
            if rn not in self.resources:
                self.err("NAME", f"transition: unknown resource '{rn}'", d)
            info = self.resources[rn]
            for st in (a, b):
                if st not in info["states"]:
                    self.err("R-203", f"transition {rn}: no state '{st}'", d)
            if a == b:
                self.err("R-203", f"transition {rn}: {a} -> {a} is meaningless", d)
            wty = None
            if d.witness is not None:
                wn, ws = d.witness
                if wn not in self.resources:
                    self.err("NAME", f"transition {rn}: unknown witness resource '{wn}'", d)
                winfo = self.resources[wn]
                if winfo["fields"]:
                    self.err("R-205", f"transition {rn}: witness '{wn}' must not carry storage (a field-less capability token)", d)
                if ws is None:
                    if len(winfo["states"]) != 1:
                        self.err("R-205", f"transition {rn}: the witness state must be written: witness {wn}<State>", d)
                    ws = winfo["states"][0]
                if ws not in winfo["states"]:
                    self.err("R-203", f"transition {rn}: resource '{wn}' has no state '{ws}'", d)
                wty = ResT(wn, frozenset({ws}))
            dev_a, dev_b = a in info["device"], b in info["device"]
            if dev_a and not dev_b and wty is None:
                self.err("R-205", f"transition {rn}: {a} -> {b} takes the buffer back from the device but asks for no completion witness", d,
                         "A buffer can be taken back from a device only with evidence that the transfer has finished; otherwise "
                         "the CPU touches it while the transfer is running (J-01).\n"
                         f"expected: transition {rn}: {a} -> {b} witness DmaDone;")
            obl = []
            if not dev_a and dev_b:
                obl = ["clean", "barrier"]
            elif dev_a and not dev_b:
                obl = ["barrier", "invalidate"]
            elif dev_a and dev_b:
                obl = ["barrier"]
            t.update(witness_ty=wty, obligations=obl)

    def check_region_capacity(self):
        for rname, reg in self.regions.items():
            used, members = 0, []
            for sname, info in self.states.items():
                ty = info["ty"]
                if not isinstance(ty, ResT):
                    continue
                rinfo = self.resources[ty.name]
                if rinfo["region"] != rname:
                    continue
                sz = rinfo["size"]
                if reg["kind"] == "dma":
                    sz = (max(sz, 1) + reg["line"] - 1) // reg["line"] * reg["line"]
                used += sz
                members.append(f"{sname} ({sz} B)")
            reg["used"] = used
            if used > reg["size"]:
                self.err("R-209", f"region '{rname}' overflows: {used} B > {reg['size']} B", reg["decl"],
                         "placed: " + ", ".join(members) + "\nCapacity is proved at compile time; the linker "
                         "script is not left to spill silently into the next region.")
            self.notes.append(f"region '{rname}' ({reg['kind']}): {used} / {reg['size']} B"
                              + (f" — {', '.join(members)}" if members else ""))

    def resource_init(self, v, ty: ResT, name: str) -> dict:
        info = self.resources[ty.name]
        init_state = info["states"][0]
        if init_state not in ty.states:
            self.err("R-203", f"state '{name}' starts in state '{init_state}' (the first state in the declaration); "
                              f"its type {ty} must include it", v)
        if not info["fields"]:
            if not (isinstance(v, A.Name) and v.name == ty.name):
                self.err("R-203", f"state '{name}': a field-less resource is initialised with its name '{ty.name}'", v)
            return {}
        if not (isinstance(v, A.StructLit) and v.name == ty.name):
            self.err("R-203", f"state '{name}': must be initialised as {ty.name} {{ field: constant, ... }}", v)
        ftys = dict(info["fields"])
        got = [f for f, _ in v.fields]
        if sorted(got) != sorted(ftys) or len(set(got)) != len(got):
            self.err("00§0.2-E", f"state '{name}': every field must be initialised exactly once", v)
        v._ty = ty
        return {f: self.state_const(fe, ftys[f], f"{name}.{f}") for f, fe in v.fields}

    def eval_resource_field(self, e, b, write: bool):
        rty = b.ty.inner if isinstance(b.ty, BorrowT) else b.ty
        if b.kind == "state":
            self.err("R-203", f"resource state '{e.left}' cannot be used directly; take({e.left}) first", e)
        info = self.resources[rty.name]
        st = rty.single()
        if st is None:
            self.err("R-203", f"{rty}: a field of a resource in an unknown state cannot be accessed; open the state with match", e)
        ftys = dict(info["fields"])
        if e.right not in ftys:
            self.err("NAME", f"resource '{rty.name}' has no field '{e.right}'", e)
        if st in info["device"]:
            self.err("R-205", f"'{e.left}' ({rty}) is held by a hardware agent: the CPU cannot {'write' if write else 'read'} it",
                     e, "The CPU touching the buffer while a transfer is running is a race, visible only on real hardware "
                        "and only depending on timing (J-01, W-06).\nThe buffer is taken back with a completion witness: "
                        "transit<Cpu>(b, done)")
        allowed = info["write"] if write else info["read"]
        if st not in allowed:
            self.err("R-203", f"in the state of '{e.left}' ({rty}) the field cannot be {'written' if write else 'read'}", e,
                     "Operations that depend on a protocol state are restricted by the typestate (R-203)."
                     + ("\nWriting a verified copy invalidates the verification token (R-208): to write, first move "
                        "to the unverified state." if write else ""))
        if write and isinstance(b.ty, BorrowT):
            self.err("R-203", f"'{e.left}' is borrowed (&{rty}): read-only", e)
        e._ref = ("rfield", e.left, e.right, isinstance(b.ty, BorrowT))
        fty = ftys[e.right]
        if isinstance(b.term, dict) and b.term.get(e.right) is not None:
            return Val(fty, b.term[e.right])
        return Val(fty, self.sym(fty, f"{e.left}.{e.right}"))

    def exec_resource_assign(self, st, env: dict) -> dict:
        tgt = st.target
        path = tgt if isinstance(tgt, A.Path) else tgt.base
        if path.sep != "." or path.left not in env or not isinstance(env[path.left].ty, (ResT, BorrowT)):
            self.err("TYPE", "only resource fields (b.field or b.field[i]) can be assigned", tgt)
        b = env[path.left]
        fv = self.eval_resource_field(path, b, write=True)
        path._ty = fv.ty
        if isinstance(tgt, A.Index):
            if not isinstance(fv.ty, ArrayT):
                self.err("TYPE", f"'{path.left}.{path.right}' is not an array", tgt)
            self.index_obligation(tgt, fv.ty, env)
            ety = fv.ty.elem
        else:
            if isinstance(fv.ty, ArrayT):
                self.err("TYPE", f"the array '{path.left}.{path.right}' cannot be assigned as a whole; write it element by element", tgt)
            ety = fv.ty
        tgt._ty = ety
        v = self.eval(st.value, env, ety)
        self.no_value_check(v, st.value)
        v = self.coerce(v, ety, st.value, f"assignment to '{path.left}.{path.right}'")
        if isinstance(tgt, A.Path) and isinstance(b.term, dict):
            env[path.left] = Binding(b.ty, {**b.term, path.right: v.term}, b.mutable, b.kind, b.taint)
        return env

    def eval_take_put(self, e, env):
        if self.cur is not None and getattr(self.cur.fn, "_prot", None) is not None:
            self.err("R-203", f"{e.func}() cannot be used inside a protected operation", e)
        if not e.args or not isinstance(e.args[0], A.Name):
            self.err("R-203", f"must be called as {e.func}(state_variable{', value' if e.func == 'put' else ''})", e)
        nm = e.args[0].name
        b = env.get(nm)
        if b is None or b.kind != "state" or not isinstance(b.ty, ResT):
            self.err("R-203", f"{e.func}: '{nm}' is not a state variable of resource type", e)
        e.args[0]._ref = "state"
        e.args[0]._ty = b.ty
        if e.func == "take":
            if len(e.args) != 1:
                self.err("R-203", "take takes one argument", e)
            e._callee = "take"
            return Val(b.ty, self.sym(b.ty, f"take({nm})"))
        if len(e.args) != 2:
            self.err("R-203", "put(state_variable, resource) takes two arguments", e)
        v = self.eval(e.args[1], env, b.ty)
        self.no_value_check(v, e.args[1])
        if isinstance(v.ty, ResT):
            e._put_state = v.ty.single()
        self.coerce(v, b.ty, e.args[1], f"put({nm}, …)")
        e._callee = "put"
        return Val(UnitT(), None)

    def eval_transit(self, e, env):
        if not e.args or len(e.args) > 2:
            self.err("R-203", "must be called as transit<State>(resource[, witness])", e)
        x = self.eval(e.args[0], env)
        self.no_value_check(x, e.args[0])
        if not isinstance(x.ty, ResT) or x.ty.single() is None:
            self.err("R-203", f"transit: expected a single-state resource, found {x.ty}", e)
        rn, a, b = x.ty.name, x.ty.single(), e.tname
        if b not in self.resources[rn]["states"]:
            self.err("R-203", f"resource '{rn}' has no state '{b}'", e)
        key = (rn, a, b)
        if key not in self.transitions:
            legal = sorted(d for (r, s0, d) in self.transitions if r == rn and s0 == a)
            self.err("R-203", f"transition {rn}: {a} -> {b} is not declared", e,
                     f"Allowed transitions from {a}: {', '.join(legal) or 'none'}. The protocol state machine "
                     "is defined by the transition declarations; an undeclared transition does not compile.")
        t = self.transitions[key]
        if t["witness_ty"] is not None:
            if len(e.args) != 2:
                self.err("R-205", f"{rn}: the transition {a} -> {b} consumes a completion witness: "
                                  f"transit<{b}>(x, {t['witness_ty'].name.lower()})", e,
                         "A buffer is taken back from a device only with evidence that the transfer has finished (J-01).")
            w = self.eval(e.args[1], env)
            self.no_value_check(w, e.args[1])
            self.coerce(w, t["witness_ty"], e.args[1], "transition witness")
        elif len(e.args) == 2:
            self.err("R-203", f"{rn}: the transition {a} -> {b} takes no witness", e)
        e._trans = (rn, a, b, list(t["obligations"]))
        nty = ResT(rn, frozenset({b}))
        return Val(nty, self.sym(nty, f"transit<{b}>"))

    def coerce_resource(self, v: Val, target, node, what: str) -> Val:
        src = v.ty
        if isinstance(target, ResT):
            if isinstance(src, ResT) and src.name == target.name and src.states <= target.states:
                return Val(target, v.term)
            if isinstance(src, BorrowT):
                self.err("R-203", f"{what}: a borrowed resource ({src}) cannot be consumed or moved", node)
            note = None
            if isinstance(src, ResT) and src.name == target.name:
                info = self.resources[src.name]
                dev = [st for st in src.states if st in info["device"]]
                note = ("Protocol violation: the resource is not in the expected state. State changes happen only through transit<…> "
                        "and declared transitions (R-203)."
                        + (f"\n{src} is held by a hardware agent (J-01)." if dev else ""))
            self.err("R-203", f"{what}: expected {target}, found {src}", node, note)
        if isinstance(target, BorrowT):
            if (isinstance(src, ResT) and src == target.inner) or (isinstance(src, BorrowT) and src == target):
                node._borrow = True
                return Val(target, v.term)
            self.err("R-203", f"{what}: expected {target}, found {src}", node)
        self.err("R-203", f"{what}: a resource ({src}) cannot be used in place of {target}", node)

    def check_resource_sig(self, fn, params, ret):
        prot = getattr(fn, "_prot", None) is not None
        for n, t, _ in params:
            if isinstance(t, (ResT, BorrowT)) and prot:
                self.err("R-203", f"protected operation '{fn.name}' cannot take a resource parameter", fn)
            if isinstance(t, ResT) and t.single() is None:
                self.err("R-203", f"parameter '{n}' of '{fn.name}': a union type ({t}) is allowed only in a state declaration", fn)
            if fn.is_extern and isinstance(t, ResT) and self.resources[t.name]["fields"]:
                self.err("R-205", f"extern '{fn.name}': resource '{n}' with storage cannot be passed by consuming it at the boundary", fn,
                         f"The device or driver borrows the address: {n}: &{t}")
        if isinstance(ret, BorrowT):
            self.err("R-203", f"'{fn.name}' cannot return a borrow", fn)
        if isinstance(ret, ResT):
            if prot:
                self.err("R-203", f"protected operation '{fn.name}' cannot return a resource", fn)
            if ret.single() is None:
                self.err("R-203", f"'{fn.name}': a union type ({ret}) cannot be returned", fn)
            if fn.is_extern and self.resources[ret.name]["fields"]:
                self.err("R-205", f"extern '{fn.name}' cannot produce a resource with storage: storage is static and comes into "
                                  "being with a state declaration", fn)

    ISR_BASE = 1000

    def check_isr_props(self, t: A.TaskDecl):
        allowed = {"vector", "level", "min_interarrival", "deadline", "wcet", "stack"}
        for k in t.props:
            if k not in allowed:
                self.err("R-306", f"interrupt '{t.name}': unknown property '{k}' ({', '.join(sorted(allowed))})", t)
        for k in ("vector", "level", "min_interarrival"):
            if k not in t.props:
                self.err("R-306", f"interrupt '{t.name}': '{k}' must be declared", t,
                         "Without a minimum inter-arrival time the processor share of an interrupt is unbounded; timing "
                         "analysis is impossible.")
        info = self.tasks[t.name]
        vec, _ = self.const_value(t.props["vector"], t)
        lvl, _ = self.const_value(t.props["level"], t)
        if not isinstance(vec, int) or vec < 0:
            self.err("R-306", f"interrupt '{t.name}': vector must be a non-negative integer", t)
        if not isinstance(lvl, int) or not (1 <= lvl <= 15):
            self.err("R-306", f"interrupt '{t.name}': level must be 1..15 (LEON3/GRLIB interrupt levels)", t)
        info["vector"], info["level"] = vec, lvl
        info["priority"] = self.ISR_BASE + lvl
        mi, mu = self.const_value(t.props["min_interarrival"], t)
        if mu is None or not mu.same_dimension(U.base("s")) or mi <= 0:
            self.err("R-306", f"interrupt '{t.name}': min_interarrival must be a positive duration", t)
        info["period"] = mi * mu.scale
        if "deadline" in t.props:
            dv, du = self.const_value(t.props["deadline"], t)
            info["deadline"] = dv * du.scale
        else:
            info["deadline"] = info["period"]
        if "wcet" in t.props:
            wv, wu = self.const_value(t.props["wcet"], t)
            info["wcet"] = wv * wu.scale
        if "stack" in t.props:
            sv, _ = self.const_value(t.props["stack"], t)
            info["stack"] = sv
        if any(isinstance(n, A.Halt) for n in _walk(t.body)):
            self.err("R-306", f"halt is not allowed inside interrupt '{t.name}'", t)

    def check_task_props(self, t: A.TaskDecl):
        if t.kind == "isr":
            return self.check_isr_props(t)
        allowed = {"period", "priority", "deadline", "stack", "wcet"}
        for k in t.props:
            if k not in allowed:
                self.err("03§3.2", f"task '{t.name}': unknown property '{k}' ({', '.join(sorted(allowed))})", t)
        for k in ("period", "priority"):
            if k not in t.props:
                self.err("03§3.2", f"task '{t.name}': '{k}' must be declared", t)
        info = self.tasks[t.name]
        pr, _ = self.const_value(t.props["priority"], t)
        if not isinstance(pr, int) or not (1 <= pr <= 255):
            self.err("03§3.2", f"task '{t.name}': priority must be an integer in 1..255", t)
        info["priority"] = pr
        for k in ("period", "deadline"):
            if k in t.props:
                v, u = self.const_value(t.props[k], t)
                if u is None or not u.same_dimension(U.base("s")) or v <= 0:
                    self.err("03§3.2", f"task '{t.name}': {k} must be a positive duration (e.g. 10[ms])", t)
                info[k] = v * u.scale
        if "wcet" in t.props:
            wv, wu = self.const_value(t.props["wcet"], t)
            if wu is None or not wu.same_dimension(U.base("s")) or wv <= 0:
                self.err("03§3.2", f"task '{t.name}': wcet must be a positive duration (e.g. 2[ms])", t)
            info["wcet"] = wv * wu.scale
        if "stack" in t.props:
            sv, su = self.const_value(t.props["stack"], t)
            if su is not None or not isinstance(sv, int) or sv <= 0:
                self.err("R-201", f"task '{t.name}': stack must be a positive number of bytes (e.g. stack = 2048)", t)
            info["stack"] = sv
        info.setdefault("deadline", info["period"])
        if info["deadline"] > info["period"]:
            self.err("03§3.2", f"task '{t.name}': the deadline ({info['deadline']*1e3:g} ms) cannot be longer than the period "
                               f"({info['period']*1e3:g} ms)", t)

    def check_encoded(self, d: A.EncodedDecl):
        if d.name in self.encodings:
            self.err("NAME", f"encoding '{d.name}' defined twice", d)
        wire, sem = self.resolve(d.wire), self.resolve(d.semantic)
        if not (isinstance(wire, IntT) and isinstance(sem, IntT)):
            self.err("R-202", "Core v1: wire and semantic types must be integer range types", d)
        w = self.sym(wire, f"{d.name}.wire")
        s = self.sym(sem, f"{d.name}.sem")
        dec = self.eval(d.dexpr, {d.dparam: Binding(wire, w, False, "param")})
        enc = self.eval(d.eexpr, {d.eparam: Binding(sem, s, False, "param")})
        if not isinstance(dec.ty, IntT) or not isinstance(enc.ty, IntT):
            self.err("R-202", "decode and encode must produce integers", d)
        self.prove(z3.And(dec.term >= sem.lo, dec.term <= sem.hi), d.dexpr, "R-202",
                   f"decode does not map the wire range onto the semantic range {sem}")
        self.prove(z3.And(enc.term >= wire.lo, enc.term <= wire.hi), d.eexpr, "R-202",
                   f"encode does not map the semantic range onto the wire range {wire}")
        self.prove(z3.substitute(dec.term, (w, enc.term)) == s, d, "R-202",
                   f"'{d.name}': decode(encode(s)) ≠ s, the encoding declaration is inconsistent",
                   "decode and encode must be inverses; otherwise the value written and the value read diverge.")
        self.prove(z3.substitute(enc.term, (s, dec.term)) == w, d, "R-202",
                   f"'{d.name}': encode(decode(w)) ≠ w, the encoding declaration is inconsistent")
        self.encodings[d.name] = {"wire": wire, "sem": sem, "w": w, "s": s, "dec": dec.term, "enc": enc.term,
                                  "decl": d}
        d._wire, d._sem = wire, sem

    def check_state(self, d: A.StateDecl):
        if d.name in self.states or d.name in self.consts or d.name in self.fns:
            self.err("NAME", f"'{d.name}' is already defined", d)
        ty = self.resolve(d.type)
        v = d.value
        if isinstance(ty, HeldT):
            if not (isinstance(v, A.Call) and v.func == "held" and len(v.args) == 1 and "max_age" in v.kwargs):
                self.err("R-403", f"a Held state must be initialised with held(initial_value, max_age = duration)", v,
                         "Every state derived from faulty or stale input has a declared maximum age.")
            init = self.state_const(v.args[0], ty.inner, d.name)
            age, unit = self.const_value(v.kwargs["max_age"], v)
            if unit is None or not unit.same_dimension(U.base("s")):
                self.err("R-403", "max_age must be a duration (e.g. 50[ms])", v)
            if age <= 0:
                self.err("R-403", "max_age must be positive", v)
            info = {"kind": "held", "init": init, "max_age_s": age * unit.scale}
        elif isinstance(ty, LatchT):
            if not (isinstance(v, A.Call) and v.func == "latch" and not v.args and "persists_for" in v.kwargs):
                self.err("R-404", "a Latch state must be initialised with latch(persists_for = N)", v,
                         "A transient signal cannot be latched without a declared persistence condition.\n"
                         "Mars Polar Lander (1999): the leg-deploy shock was latched as 'touchdown'.")
            n, _ = self.const_value(v.kwargs["persists_for"], v)
            if not isinstance(n, int) or n < 1:
                self.err("R-404", "persists_for must be a positive integer (number of consecutive events)", v)
            info = {"kind": "latch", "need": n}
        elif isinstance(ty, ResT):
            info = {"kind": "resource", "init": self.resource_init(v, ty, d.name)}
        elif isinstance(ty, BorrowT):
            self.err("R-203", f"state '{d.name}' cannot be a borrow", d)
        else:
            info = {"kind": "scalar", "init": self.state_const(v, ty, d.name)}
        info["ty"] = ty
        info["decl"] = d
        d._ty = ty
        self.states[d.name] = info

    def state_const(self, e, ty, name):
        if isinstance(ty, ArrayT):
            if not (isinstance(e, A.ArrayRepeat) and e.n == ty.n):
                self.err("TYPE", f"state '{name}': must be initialised as [constant; {ty.n}]", e)
            v = self.state_const(e.value, ty.elem, name)
            e._ty = ty
            return [v] * ty.n
        if isinstance(ty, BoolT):
            if not isinstance(e, A.BoolLit):
                self.err("TYPE", f"state '{name}': expected a bool constant", e)
            return e.value
        if isinstance(ty, EnumT):
            if not (isinstance(e, A.Path) and e.sep == "::" and e.left == ty.name and e.right in self.enums[ty.name]):
                self.err("TYPE", f"state '{name}': expected {ty.name}::Variant", e)
            return self.enums[ty.name].index(e.right)
        if isinstance(ty, RecordT):
            if not (isinstance(e, A.StructLit) and e.name == ty.name):
                self.err("TYPE", f"state '{name}': must be initialised as {ty.name} {{ field: constant, ... }}", e)
            self.eval_struct_lit(e, {})
            ftys = dict(self.structs[ty.name]["fields"])
            e._ty = ty
            return {f: self.state_const(x, ftys[f], f"{name}.{f}") for f, x in e.fields}
        if isinstance(ty, FixT):
            if not isinstance(e, A.Requantize):
                self.err("TYPE", f"state '{name}': a fixed-point initialiser must be requantize<...>(constant)", e)
            v = self.eval_requantize(e, {})
            self.coerce(v, ty, e, f"state '{name}'")
            return e._const_raw
        val, unit = self.const_value(e, e)
        self.check_const_value(ty, val, unit, e, f"state '{name}'", "R-501")
        return val

    def check_recursion(self):
        graph = {n: sorted({c.func for c in _walk(f.body) if isinstance(c, A.Call) and c.func in self.fns})
                 for n, f in self.fns.items() if not f.is_extern}
        state: dict[str, int] = {}
        stack: list[str] = []

        def dfs(n):
            state[n] = 1
            stack.append(n)
            for m in graph.get(n, []):
                if state.get(m) == 1:
                    cyc = stack[stack.index(m):] + [m]
                    for f in cyc[:-1]:
                        if self.fns[f].recursive is None:
                            self.err("R-201", f"recursion: {' → '.join(cyc)}, the stack depth cannot be proved",
                                     self.fns[f],
                                     "Unbounded recursion is forbidden. Either turn it into a loop or "
                                     "declare a bound with `recursive(depth <= N)`.")
                    self.notes.append(f"recursion accepted (bound declared; no proof of the bound in Core v0): "
                                      f"{' → '.join(cyc)}")
                elif state.get(m) is None:
                    dfs(m)
            stack.pop()
            state[n] = 2

        for n in graph:
            if n not in state:
                dfs(n)

    def fn_modes(self, fn: A.FnDecl) -> frozenset:
        allm = frozenset(self.modes) if self.modes else frozenset({"*"})
        for e in fn.effects:
            if e.kind == "mode":
                for m in e.value:
                    if m not in self.modes:
                        self.err("05§5.5", f"unknown mode '{m}'", e)
                return frozenset(e.value)
        return allm

    def make_sig(self, fn: A.FnDecl) -> Sig:
        params, env = [], {}
        seen = set()
        for p in fn.params:
            if p.name in seen:
                self.err("NAME", f"parameter '{p.name}' defined twice", p)
            seen.add(p.name)
            ty = self.resolve(p.type)
            s = self.sym(ty, p.name)
            params.append((p.name, ty, s))
            env[p.name] = Binding(ty, s, False, "param")
            p._ty = ty
        ret = self.resolve(fn.ret) if fn.ret else UnitT()
        self.check_resource_sig(fn, params, ret)
        sig = Sig(fn, params, ret, self.fn_modes(fn), [], [], None, [], None)
        saved = self.cur
        self.cur = sig
        for r in fn.requires:
            v = self.eval(r, env)
            self.expect_bool(v, r, "requires")
            sig.requires.append((v.term, r))
        for a in fn.annotations:
            if a.name == "valid_for":
                for arg in a.args:
                    if isinstance(arg, tuple):
                        if arg[0] == "validated_by" and isinstance(arg[1], A.StrLit):
                            sig.validated_by = arg[1].value
                        continue
                    v = self.eval(arg, env)
                    self.expect_bool(v, arg, "@valid_for")
                    sig.valid_for.append((v.term, arg))
        if fn.ensures:
            if isinstance(ret, UnitT):
                self.err("06§6.3", "ensures is meaningless in a function without a return value", fn)
            rs = self.sym(ret, "result")
            sig.result_sym = rs
            env2 = dict(env)
            env2["result"] = Binding(ret, rs, False, "param")
            for en in fn.ensures:
                v = self.eval(en, env2)
                self.expect_bool(v, en, "ensures")
                sig.ensures.append((v.term, en))
        self.cur = saved
        return sig

    def check_fn(self, sig: Sig):
        fn = sig.fn
        self.cur = sig
        self.pc = [t for t, _ in sig.requires] + [t for t, _ in sig.valid_for]
        env = {n: Binding(t, s, False, "param") for n, t, s in sig.params}
        prot = getattr(fn, "_prot", None)
        if prot is not None:
            for fname, finfo in self.protected[prot]["fields"].items():
                env[fname] = Binding(finfo["ty"], self.sym(finfo["ty"], f"{prot}.{fname}"), True, "pfield")
        else:
            for sname, info in self.states.items():
                env[sname] = Binding(info["ty"], self.sym(info["ty"], sname), True, "state")
        self.pc_taint = False
        for h in _walk(fn.body):
            if isinstance(h, A.Halt):
                self.check_halt(h, sig)
        env, term, tail = self.exec_block(fn.body, env)
        if not term:
            if isinstance(sig.ret, UnitT):
                pass
            elif tail is None:
                self.err("TYPE", f"'{fn.name}' must return {sig.ret} but its body ends without producing a value", fn)
            else:
                self.check_return(tail, fn.body.tail or fn.body, sig)
        self.pc = []
        self.cur = None

    def check_return(self, v: Val, node, sig: Sig):
        v2 = self.coerce(v, sig.ret, node, "return value")
        for term, en in sig.ensures:
            goal = z3.substitute(term, (sig.result_sym, v2.term)) if sig.result_sym is not None else term
            self.prove(goal, node, "06§6.3", f"the postcondition (ensures) of '{sig.fn.name}' could not be proved")
        return v2

    def check_halt(self, h: A.Halt, sig: Sig):
        if h.because is None or h.allowed_in is None:
            self.err("R-401", "`halt` cannot be written without a justification and a set of allowed modes", h,
                     "expected: halt(because = \"...\", allowed_in = { ... });\n"
                     "Ariane 5 Flight 501 (1996): the SRI shut itself down when it detected the overflow, "
                     "a sensible policy before lift-off and a disaster in flight.")
        for m in h.allowed_in:
            if m not in self.modes:
                self.err("R-401", f"halt: unknown mode '{m}'", h)
        bad = set(h.allowed_in) & self.flight
        if bad:
            self.err("R-401", f"`halt` cannot be allowed in modes of the flight phase: {sorted(bad)}", h,
                     "In flight_phase modes, stopping is not a response to a fault but the avoidance of one. "
                     "Choose a fallback: degrade, hold_last, failover, safe_mode.")
        if not sig.modes <= set(h.allowed_in):
            extra = sorted(sig.modes - set(h.allowed_in))
            self.err("R-401", f"function '{sig.fn.name}' can be called from modes in which halt is not allowed: {extra}",
                     h, "Restrict the function to the allowed modes with `effects { mode(...) }`.")

    def check_invariant_cond(self, cond, node):
        if self.bmc_mode or not getattr(self, "dead_code_check", True):
            return
        for side, t in (("false", cond), ("true", z3.Not(cond))):
            if not self.sat_under_path(t):
                cfg = any((isinstance(n, A.Name) and getattr(n, "_ref", None) == "const")
                          or (isinstance(n, A.Path) and getattr(n, "_ref", ("",))[0] == "config")
                          for n in _walk(node))
                where = f"line {getattr(node, 'line', 0)}"
                if cfg:
                    self.notes.append(f"deactivated code ({where}): in this configuration the condition is always "
                                      f"{'true' if side == 'false' else 'false'} (DO-178C deactivated code)")
                    return
                always = "true" if side == "false" else "false"
                self.err("R-606", f"the condition is always {always} on this path; the other branch is dead code", node,
                         "The types and the path condition already decide the result of this comparison. Dead code cannot be verified "
                         "and must be removed for certification (DO-178C §6.4.4.3; MISRA C:2012 Rule 14.3).\n"
                         "If a defensive check seemed necessary, the value range is already proved: delete the check.")

    def exec_block(self, block: A.Block, env: dict, expected=None):
        local = dict(env)
        for idx, st in enumerate(block.stmts):
            local, term = self.exec_stmt(st, local)
            if term:
                rest = block.stmts[idx + 1:] + ([block.tail] if block.tail is not None else [])
                if rest:
                    self.err("R-606", "unreachable statement: the previous statement returns or halts on every path", rest[0],
                             "Unreachable code cannot be checked and is dead code for certification (DO-178C §6.4.4.3).")
                return self._restore(env, local), True, None
        tail = None
        if block.tail is not None:
            if isinstance(block.tail, (A.IfExpr, A.Match)):
                tail, local = self.branch_eval(block.tail, local, expected)
            else:
                tail = self.eval(block.tail, local, expected)
            if tail is None:
                return self._restore(env, local), True, None
        return self._restore(env, local), False, tail

    @staticmethod
    def _restore(outer: dict, inner: dict) -> dict:
        return {k: inner[k] for k in outer}

    def declare(self, env: dict, name: str, b: Binding, node):
        if name in env or name in self.consts or name in self.configs or name in self.fns or name in self.states:
            self.err("NAME", f"'{name}' is already defined; shadowing is forbidden", node,
                     "Two meanings for the same name are a source of review errors (MISRA C:2012 Rule 5.3).")
        env[name] = b

    def exec_stmt(self, st, env: dict):
        if isinstance(st, A.Let):
            v = self.eval(st.value, env, self.resolve(st.type) if st.type else None)
            self.no_value_check(v, st.value)
            if st.type is not None:
                ty = self.resolve(st.type)
                v = self.coerce(v, ty, st.value, f"'{st.name}'")
            st._ty = v.ty
            self.declare(env, st.name, Binding(v.ty, v.term, False, taint=v.taint or self.pc_taint), st)
            return env, False
        if isinstance(st, A.Var):
            ty = self.resolve(st.type)
            v = self.eval(st.value, env, ty)
            self.no_value_check(v, st.value)
            v = self.coerce(v, ty, st.value, f"'{st.name}'")
            st._ty = ty
            self.declare(env, st.name, Binding(ty, v.term, True, taint=v.taint or self.pc_taint), st)
            return env, False
        if isinstance(st, A.Assign):
            return self.exec_assign(st, env), False
        if isinstance(st, A.Return):
            sig = self.cur
            if st.value is None:
                if not isinstance(sig.ret, UnitT):
                    self.err("TYPE", f"'{sig.fn.name}' must return {sig.ret}", st)
            else:
                v = self.eval(st.value, env, sig.ret)
                self.no_value_check(v, st.value)
                v2 = self.check_return(v, st.value, sig)
                if self.bmc_returns is not None:
                    self.bmc_returns.append((z3.And(*self.pc) if self.pc else z3.BoolVal(True), v2.term,
                                             {s: env[s].term for s in self.states if s in env}))
            return env, True
        if isinstance(st, A.Halt):
            return env, True
        if isinstance(st, A.For):
            return self.exec_for(st, env), False
        if isinstance(st, A.ExprStmt):
            e = st.expr
            if isinstance(e, (A.IfExpr, A.Match)):
                v, env2 = self.branch_eval(e, env, None, stmt=True)
                return env2, v is None
            if isinstance(e, A.Call) and e.func == "push":
                self.err("JPL-7", "the result of push cannot be ignored: the queue-full case (Full) must be handled",
                         e, "Spirit rover (2004): a structure that grew without bound exhausted the memory. "
                            "Capacity is part of the type; running full is an engineering decision.\n"
                            "expected: match push(q, v) { Ok => ..., Full => ... }")
            v = self.eval(e, env)
            if v is not None and not isinstance(v.ty, UnitT):
                self.err("JPL-7", f"a value ({v.ty}) cannot be ignored: return values must be used", e,
                         "JPL 'Power of Ten' rule 7.")
            return env, False
        self.err("INTERNAL", f"unknown statement {type(st).__name__}", st)

    def no_value_check(self, v, node):
        if v is None:
            self.err("TYPE", "this expression produces no value (every branch returns)", node)
        if isinstance(v.ty, UnitT):
            self.err("TYPE", "expected a value, found ()", node)

    def exec_assign(self, st: A.Assign, env: dict) -> dict:
        tgt = st.target
        if isinstance(tgt, A.Path) or (isinstance(tgt, A.Index) and isinstance(tgt.base, A.Path)):
            return self.exec_resource_assign(st, env)
        if isinstance(tgt, A.Name):
            b = env.get(tgt.name)
            if b is None:
                self.err("NAME", f"undefined variable '{tgt.name}'", tgt)
            if not b.mutable:
                self.err("TYPE", f"'{tgt.name}' cannot be changed (let); use var for a mutable variable", tgt)
            if isinstance(b.ty, (HeldT, LatchT)):
                self.err("TYPE", f"'{tgt.name}' ({b.ty}) cannot be assigned directly; use refresh/feed/reset", tgt)
            v = self.eval(st.value, env, b.ty)
            self.no_value_check(v, st.value)
            v = self.coerce(v, b.ty, st.value, f"assignment to '{tgt.name}'")
            if b.kind in ("state", "pfield"):
                self.check_state_write(tgt.name, b.ty, v, st)
            env[tgt.name] = Binding(b.ty, v.term, True, b.kind,
                                    taint=False if b.kind in ("state", "pfield") else (v.taint or self.pc_taint))
            tgt._ty = b.ty
            tgt._ref = b.kind if b.kind in ("state", "pfield") else "local"
            if b.kind == "pfield":
                tgt._obj = self.cur.fn._prot
            return env
        if isinstance(tgt, A.Index):
            if not isinstance(tgt.base, A.Name):
                self.err("TYPE", "in Core v0 only named arrays can be assigned", tgt)
            b = env.get(tgt.base.name)
            if b is None:
                self.err("NAME", f"undefined variable '{tgt.base.name}'", tgt)
            if not b.mutable:
                self.err("TYPE", f"'{tgt.base.name}' cannot be changed", tgt)
            if not isinstance(b.ty, ArrayT):
                self.err("TYPE", f"'{tgt.base.name}' is not an array", tgt)
            self.index_obligation(tgt, b.ty, env)
            v = self.eval(st.value, env, b.ty.elem)
            self.no_value_check(v, st.value)
            v = self.coerce(v, b.ty.elem, st.value, "array element assignment")
            if b.kind == "state":
                self.check_state_write(tgt.base.name, b.ty.elem, v, st)
            elif v.taint or self.pc_taint:
                env[tgt.base.name] = Binding(b.ty, b.term, True, b.kind, taint=True)
            tgt._ty = b.ty.elem
            tgt.base._ty = b.ty
            tgt.base._ref = "state" if b.kind == "state" else "local"
            return env

    def check_state_write(self, name, ty, v: Val, node):
        if isinstance(ty, BoolT) and (v.taint or self.pc_taint):
            self.err("R-404", f"a transient signal is being latched: persistent '{name}' is changed under a condition that comes "
                              "from a sensor", node,
                     "A transient signal (shock, EMI, SEU) cannot become a persistent decision without a declared "
                     "persistence condition.\nMars Polar Lander (1999): the leg-deploy shock was latched as 'touchdown', "
                     "and the engines shut down at about 40 m.\n"
                     "expected: state x: Latch = latch(persists_for = N); ... feed(x, condition);")
        if v.taint:
            self.err("R-403", f"a value derived from a sensor is written to persistent '{name}' without an age limit", node,
                     "Every state derived from faulty or stale input has a declared maximum age; "
                     "otherwise a corrupt value persists without limit.\nSchiaparelli (2016): a corrupt attitude "
                     "estimate persisted and the vehicle believed it had landed.\n"
                     "expected: state x: Held<T> = held(initial, max_age = duration); ... refresh(x, value, now);")

    def exec_for(self, st: A.For, env: dict) -> dict:
        if st.var in env:
            self.err("NAME", f"'{st.var}' is already defined; shadowing is forbidden", st)
        assigned = _assigned_names(st.body)
        if st.lo >= st.hi:
            return env
        inv = st.invariant
        rng = IntT(st.lo, st.hi - 1)

        def inv_at(env_, ival, ity):
            e2 = dict(env_)
            e2[st.var] = Binding(ity, ival, False)
            v = self.eval(inv, e2)
            self.expect_bool(v, inv, "loop invariant")
            return v.term

        if inv is not None:
            self.prove(inv_at(env, z3.IntVal(st.lo), IntT(st.lo, st.lo)), inv, "06§6.3",
                       "the loop invariant could not be proved on entry")
        head = self.havoc(env, assigned, "loop entry")
        i = self.sym(rng, st.var)
        saved = list(self.pc)
        if inv is not None:
            self.pc = saved + [inv_at(head, i, rng)]
        inner = dict(head)
        inner[st.var] = Binding(rng, i, False)
        out_env, term, _ = self.exec_block(st.body, inner)
        if inv is not None and not term:
            self.prove(inv_at(out_env, i + 1, IntT(st.lo + 1, st.hi)), inv, "06§6.3",
                       "the loop invariant is not preserved by the body")
        self.pc = saved
        after = self.havoc(env, assigned, "loop end")
        if inv is not None:
            self.pc = saved + [inv_at(after, z3.IntVal(st.hi), IntT(st.hi, st.hi))]
        return after

    def havoc(self, env: dict, names, why: str) -> dict:
        env = dict(env)
        for n in names:
            b = env.get(n)
            if b is None or not b.mutable:
                continue
            env[n] = Binding(b.ty, self.sym(b.ty, f"_{n}@{why}"), True, b.kind, b.taint)
        return env

    def merge(self, cond, a: dict, b: dict) -> dict:
        out = {}
        for k in a:
            ba, bb = a[k], b[k]
            out[k] = Binding(ba.ty, _ite(cond, ba.term, bb.term), ba.mutable, ba.kind, ba.taint or bb.taint)
        return out

    def branch_eval(self, e, env: dict, expected, stmt: bool = False):
        saved = list(self.pc)
        saved_taint = self.pc_taint
        if isinstance(e, A.IfExpr):
            c = self.eval(e.cond, env)
            self.expect_bool(c, e.cond, "if condition")
            self.check_invariant_cond(c.term, e.cond)
            arms = [(c.term, e.then, None), (z3.Not(c.term), e.els, None)]
            arm_taint = c.taint
        else:
            arms = self.match_arms(e, env)
            arm_taint = e._scrut_taint
        outs = []
        for cond, body, bind in arms:
            self.pc = saved + [cond]
            self.pc_taint = saved_taint or arm_taint
            inner = dict(env)
            if bind is not None:
                self.declare(inner, bind[0], bind[1], bind[2])
            if body is None:
                v, envo = Val(UnitT(), None), dict(env)
            elif isinstance(body, A.Block):
                envo, term, tail = self.exec_block(body, inner, expected)
                v = None if term else (tail if tail is not None else Val(UnitT(), None))
            elif isinstance(body, (A.IfExpr, A.Match)):
                v, envo = self.branch_eval(body, inner, expected, stmt)
            else:
                v = self.eval(body, inner, expected)
                envo = inner
            outs.append((cond, v, self._restore(env, envo), body))
        self.pc_taint = saved_taint
        live = [o[:3] for o in outs if o[1] is not None]
        live_bodies = [o[3] for o in outs if o[1] is not None]
        if not live:
            self.pc = saved
            e._ty = UnitT()
            return None, env
        merged = live[-1][2]
        for cond, _, envo in reversed(live[:-1]):
            merged = self.merge(cond, envo, merged)
        self.pc = saved + [z3.Or(*[c for c, _, _ in live])] if len(live) < len(outs) else saved
        if stmt or all(isinstance(v.ty, UnitT) for _, v, _ in live):
            e._ty = UnitT()
            return Val(UnitT(), None), merged
        if isinstance(e, A.IfExpr) and e.els is None:
            self.err("TYPE", "an if without an else branch cannot produce a value", e)
        live = self.unify_float_bits(live, live_bodies, expected, e)
        live = self.unify_literal_frames(live, live_bodies, expected)
        v = self.join([(c, v, None) for c, v, _ in live], e, expected)
        v.taint = arm_taint or any(x.taint for _, x, _ in live)
        e._ty = v.ty
        return v, merged

    def expect_bool(self, v, node, what):
        if v is None or not isinstance(v.ty, BoolT):
            self.err("TYPE", f"{what} must be bool, found {v.ty if v else '()'}", node)

    def eval(self, e, env: dict, expected=None, stmt=False):
        v = self._eval(e, env, expected, stmt)
        if v is not None:
            e._ty = v.ty
            if not isinstance(e, A.Call) or getattr(e, "_callee", "") not in ("is_set",):
                v.taint = (v.taint or isinstance(v.ty, (SuspectT, HeldReadT))
                           or any(getattr(c, "_taint", False) for c in _expr_children(e)))
            e._taint = v.taint
        return v

    def _eval(self, e, env, expected, stmt):
        if isinstance(e, A.IntLit):
            if not (INT64_MIN <= e.value <= INT64_MAX):
                self.err("R-501", "integer literal outside the int64 range", e)
            return Val(IntT(e.value, e.value), z3.IntVal(e.value))
        if isinstance(e, A.FloatLit):
            bits = expected.bits if isinstance(expected, FloatT) else 64
            e._bits = bits
            v = self.f32(e.value) if bits == 32 else e.value
            return Val(FloatT(e.unit, None, v, v, bits), self.fval(e.value, bits))
        if isinstance(e, A.BoolLit):
            return Val(BoolT(), z3.BoolVal(e.value))
        if isinstance(e, A.StrLit):
            return Val(StrT(), None)
        if isinstance(e, A.Name):
            return self.eval_name(e, env)
        if isinstance(e, A.Path):
            self._path_env = env
            return self.eval_path(e)
        if isinstance(e, A.Unary):
            return self.eval_unary(e, env, expected)
        if isinstance(e, A.Binary):
            return self.eval_binary(e, env)
        if isinstance(e, A.Call):
            return self.eval_call(e, env, expected)
        if isinstance(e, A.Index):
            return self.eval_index(e, env)
        if isinstance(e, A.ArrayRepeat):
            ev = expected.elem if isinstance(expected, ArrayT) else None
            v = self.eval(e.value, env, ev)
            self.no_value_check(v, e.value)
            elem = ev if ev is not None else v.ty
            self.coerce(v, elem, e.value, "array initialiser element")
            ty = ArrayT(elem, e.n)
            self.array_types.add(ty)
            return Val(ty, None)
        if isinstance(e, A.Narrow):
            return self.eval_narrow(e, env)
        if isinstance(e, A.Requantize):
            return self.eval_requantize(e, env)
        if isinstance(e, A.GenericCall):
            return self.eval_wire_builtin(e, env)
        if isinstance(e, A.ProtCall):
            return self.eval_prot_call(e, env)
        if isinstance(e, A.StructLit):
            return self.eval_struct_lit(e, env)
        if isinstance(e, A.Convert):
            return self.eval_convert(e, env)
        if isinstance(e, (A.Match, A.IfExpr)):
            outer_mut = {k for k, b in env.items() if b.mutable}
            bad = _assigned_names(e) & outer_mut
            if bad:
                self.err("TYPE", f"assignment to an outer variable inside a value-producing if/match is not allowed: {sorted(bad)}", e,
                         "Core v0: move the assignment into an if/match in statement position.")
            v, _ = self.branch_eval(e, env, expected)
            return v
        if isinstance(e, A.Block):
            _, term, tail = self.exec_block(e, env)
            if term:
                return None
            return tail if tail is not None else Val(UnitT(), None)
        self.err("INTERNAL", f"unknown expression {type(e).__name__}", e)

    def eval_name(self, e: A.Name, env):
        if e.name in env:
            b = env[e.name]
            if isinstance(b.ty, ResT) and b.kind == "state":
                self.err("R-203", f"resource state '{e.name}' cannot be used directly; take it with take({e.name}), "
                                  f"put it back with put({e.name}, …)", e)
            e._ref = b.kind if b.kind in ("state", "pfield") else "local"
            if b.kind == "pfield":
                e._obj = self.cur.fn._prot
            return Val(b.ty, b.term, b.taint)
        if e.name in self.consts:
            e._ref = "const"
            v = self.consts[e.name]
            cv = getattr(self.const_decls.get(e.name), "_cval", None)
            if isinstance(v.ty, IntT) and isinstance(cv, int):
                return Val(IntT(cv, cv), v.term)
            if isinstance(v.ty, FloatT) and isinstance(cv, (int, float)):
                fv = self.f32(float(cv)) if v.ty.bits == 32 else float(cv)
                return Val(FloatT(v.ty.unit, v.ty.frame, fv, fv, v.ty.bits), v.term)
            return Val(v.ty, v.term)
        if e.name in SUSPECT_TAGS or e.name in PUSH_TAGS:
            self.err("TYPE", f"'{e.name}' can be used only as a match pattern", e)
        self.err("NAME", f"undefined name '{e.name}'", e)

    def eval_path(self, e: A.Path):
        if e.sep == "::":
            if e.left not in self.enums:
                self.err("NAME", f"unknown enum '{e.left}'", e)
            vs = self.enums[e.left]
            if e.right not in vs:
                self.err("NAME", f"'{e.left}' has no variant '{e.right}'", e)
            e._ref = ("enum", e.left, vs.index(e.right))
            return Val(EnumT(e.left), z3.IntVal(vs.index(e.right)))
        if e.left in env_or_empty(self) and isinstance(self._path_env[e.left].ty, (ResT, BorrowT)):
            return self.eval_resource_field(e, self._path_env[e.left], write=False)
        if e.left in env_or_empty(self) and isinstance(self._path_env[e.left].ty, RecordT):
            b = self._path_env[e.left]
            info = self.structs[b.ty.name]
            ftys = dict(info["fields"])
            if e.right not in ftys:
                self.err("NAME", f"record '{b.ty.name}' has no field '{e.right}'", e)
            e._ref = ("field", e.left, e.right, "state" if b.kind == "state" else "local")
            return Val(ftys[e.right], b.term[e.right], b.taint)
        if e.left not in self.configs:
            self.err("NAME", f"unknown configuration '{e.left}'", e)
        if e.right not in self.configs[e.left]:
            self.err("NAME", f"'{e.left}' has no field '{e.right}'", e)
        ty, term, v = self.configs[e.left][e.right]
        e._ref = ("config", e.left, e.right)
        if isinstance(ty, IntT):
            ty = IntT(v, v)
        elif isinstance(ty, FloatT):
            fv = self.f32(float(v)) if ty.bits == 32 else float(v)
            ty = FloatT(ty.unit, ty.frame, fv, fv, ty.bits)
        return Val(ty, term)

    def eval_unary(self, e: A.Unary, env, expected=None):
        v = self.eval(e.operand, env, expected if e.op == "-" and isinstance(expected, FloatT) else None)
        self.no_value_check(v, e.operand)
        if e.op == "!":
            self.expect_bool(v, e.operand, "operand of '!'")
            return Val(BoolT(), z3.Not(v.term))
        if isinstance(v.ty, IntT):
            return self.int_result(IntT(-v.ty.hi, -v.ty.lo), -v.term, e)
        if isinstance(v.ty, FixT):
            r = self.int_result(IntT(-v.ty.hi, -v.ty.lo), -v.term, e)
            return Val(FixT(v.ty.frac, v.ty.unit, r.ty.lo, r.ty.hi), r.term)
        if isinstance(v.ty, FloatT):
            t = v.ty
            return Val(FloatT(t.unit, t.frame, None if t.lo is None else -t.hi,
                              None if t.lo is None else -t.lo, t.bits), -v.term)
        self.err("TYPE", f"'-' is not defined on {v.ty}", e)

    def int_result(self, ty: IntT, term, node) -> Val:
        if fits_int64(ty):
            return Val(ty, term)
        self.prove(z3.And(term >= INT64_MIN, term <= INT64_MAX), node, "R-501",
                   "an intermediate value may exceed the int64 range (it would be undefined behaviour in the generated C)")
        return Val(IntT(max(ty.lo, INT64_MIN), min(ty.hi, INT64_MAX)), term)

    def unit_mismatch(self, a: FloatT, b: FloatT, node, what: str):
        if a.unit.same_dimension(b.unit):
            ratio = a.unit.scale / b.unit.scale
            self.err("05§5.2", f"{what}: unit mismatch, {a.unit} given, {b.unit} expected; same dimension, "
                               f"different scale (1 {a.unit} = {ratio:.6g} {b.unit})", node,
                     "There is no implicit scale conversion. Write it explicitly: convert<" + str(b.unit) + ">(...)\n"
                     "Mars Climate Orbiter (1999): lbf·s was mixed with N·s, a factor of 4.45, and the vehicle was lost.")
        self.err("05§5.2", f"{what}: dimension mismatch, {a.unit} and {b.unit}", node)

    def unify_float_bits(self, live, bodies, expected, node):
        fl = [(i, v) for i, (_, v, _) in enumerate(live) if isinstance(v.ty, FloatT)]
        if len({v.ty.bits for _, v in fl}) <= 1:
            return live

        def result_node(b):
            while isinstance(b, A.Block):
                b = b.tail
            return b
        fixed = {v.ty.bits for i, v in fl if _float_literal(result_node(bodies[i])) is None}
        want = expected.bits if isinstance(expected, FloatT) else (fixed.pop() if len(fixed) == 1 else None)
        if want is None:
            self.err("05§5.1", "the arms of a branching expression mix binary32 and binary64 (there is no implicit precision conversion)",
                     node, "Bring the arms to the same precision; the conversion is written explicitly with narrow<...>(...).")
        out = list(live)
        for i, v in fl:
            if v.ty.bits != want:
                rn = result_node(bodies[i])
                if _float_literal(rn) is None:
                    self.err("05§5.1", "the arms of a branching expression mix binary32 and binary64", node,
                             "Bring the arms to the same precision; the conversion is written explicitly with narrow<...>(...).")
                c, _, envo = out[i]
                out[i] = (c, self.retype_literal(rn, want), envo)
        return out

    def unify_literal_frames(self, live, bodies, expected):
        def result_node(b):
            while isinstance(b, A.Block):
                b = b.tail
            return b
        frames = {v.ty.frame for _, v, _ in live if isinstance(v.ty, FloatT) and v.ty.frame is not None}
        if isinstance(expected, FloatT) and expected.frame is not None:
            frames.add(expected.frame)
        if len(frames) != 1:
            return live
        f = frames.pop()
        out = list(live)
        for i, (c, v, envo) in enumerate(live):
            if isinstance(v.ty, FloatT) and v.ty.frame is None and _float_literal(result_node(bodies[i])) is not None:
                t = v.ty
                out[i] = (c, Val(FloatT(t.unit, f, t.lo, t.hi, t.bits), v.term, v.taint), envo)
        return out

    def retype_literal(self, node, bits: int) -> Val:
        lit = _float_literal(node)
        neg = lit is not node
        lit._bits = bits
        v = self.f32(lit.value) if bits == 32 else lit.value
        t = self.fval(lit.value, bits)
        ty = FloatT(lit.unit, None, v, v, bits)
        lit._ty = ty
        if neg:
            ty = FloatT(lit.unit, None, -v, -v, bits)
            t = -t
            node._ty = ty
        return Val(ty, t)

    def eval_binary(self, e: A.Binary, env):
        if e.op in ("&&", "||"):
            a = self.eval(e.left, env)
            self.expect_bool(a, e.left, f"operand of '{e.op}'")
            saved = list(self.pc)
            self.pc = saved + [a.term if e.op == "&&" else z3.Not(a.term)]
            b = self.eval(e.right, env)
            self.pc = saved
            self.expect_bool(b, e.right, f"operand of '{e.op}'")
            return Val(BoolT(), z3.And(a.term, b.term) if e.op == "&&" else z3.Or(a.term, b.term))
        a = self.eval(e.left, env)
        self.no_value_check(a, e.left)
        b = self.eval(e.right, env)
        self.no_value_check(b, e.right)
        for side, v in ((e.left, a), (e.right, b)):
            if isinstance(v.ty, WireT):
                self.err("R-202", f"an encoded wire value ({v.ty}) cannot be used in arithmetic before it is decoded", side,
                         "The wire representation differs from the semantic value; the conversion lives in one declaration.\n"
                         "expected: decode(x)")
            if isinstance(v.ty, ClampedT):
                self.err("R-407", f"a saturated command ({v.ty}) cannot be used before handling whether it was clamped", side,
                         "expected: match c { Unclamped(v) => ..., Clamped(v) => ... }; the saturated command "
                         "must be fed back into the controller state (integrator windup).")
            if isinstance(v.ty, SuspectT):
                self.err("R-402", f"a suspect value ({v.ty}) cannot be used directly", side,
                         "A measurement that may be saturated, stale or invalid cannot enter a derived state "
                         "without being handled explicitly.\nSchiaparelli (2016): a saturated measurement flowed into the estimate, "
                         "and the parachute was released at 3.7 km.\nexpected: match x { Valid(v) => ..., "
                         "Saturated(v) => ..., Stale(v) => ..., Invalid => ... }")
        op = e.op
        if op in ("==", "!=", "<", "<=", ">", ">="):
            return self.eval_compare(e, a, b)
        ta, tb = a.ty, b.ty
        if isinstance(ta, FixT) or isinstance(tb, FixT):
            return self.eval_fix_arith(e, a, b)
        if isinstance(ta, IntT) and isinstance(tb, IntT):
            return self.eval_int_arith(e, a, b)
        if isinstance(ta, FloatT) and isinstance(tb, FloatT):
            if ta.bits != tb.bits:
                for side, other, idx in ((e.right, ta, 1), (e.left, tb, 0)):
                    if _float_literal(side) is not None:
                        v = self.retype_literal(side, other.bits)
                        a, b = (a, v) if idx == 1 else (v, b)
                        break
                else:
                    self.err("05§5.1", f"'{op}': {ta} and {tb} cannot be mixed (there is no implicit precision conversion)", e,
                             "Converting binary32 ↔ binary64 involves rounding; write it explicitly with narrow<...>(...).")
            return self.eval_float_arith(e, a, b)
        if isinstance(ta, InstantT) or isinstance(tb, InstantT):
            return self.eval_instant_arith(e, a, b)
        if (isinstance(ta, IntT) and isinstance(tb, FloatT)) or (isinstance(ta, FloatT) and isinstance(tb, IntT)):
            self.err("00§0.3", f"there is no implicit numeric conversion: {ta} {op} {tb}", e,
                     "Ariane 5 Flight 501 class. A conversion must be an explicit operation that creates a proof obligation.")
        self.err("TYPE", f"operator '{op}' is not defined on {ta} and {tb}", e)

    def fix_same_scale(self, ta: FixT, tb: FixT, node, what):
        if ta.frac != tb.frac:
            self.err("05§5.1", f"{what}: fixed-point values with different scales (fraction {ta.frac} and {tb.frac}); there is no "
                               "implicit rescaling", node,
                     "Write it explicitly: requantize<Fix<...>>(x, rounding = nearest_even | toward_zero | floor | ceil)")
        if not ta.unit.same_unit(tb.unit):
            self.err("05§5.2", f"{what}: unit mismatch, {ta.unit} and {tb.unit}", node)

    def eval_fix_arith(self, e, a: Val, b: Val):
        ta, tb = a.ty, b.ty
        x, y = a.term, b.term
        op = e.op
        if isinstance(ta, FixT) and isinstance(tb, FixT):
            if op in ("+", "-"):
                self.fix_same_scale(ta, tb, e, f"'{op}'")
                lo, hi = (ta.lo + tb.lo, ta.hi + tb.hi) if op == "+" else (ta.lo - tb.hi, ta.hi - tb.lo)
                r = self.int_result(IntT(lo, hi), x + y if op == "+" else x - y, e)
                return Val(FixT(ta.frac, ta.unit, r.ty.lo, r.ty.hi), r.term)
            if op == "*":
                if ta.frac + tb.frac > 62:
                    self.err("05§5.1", "the fraction bits of the product exceed 62; requantize first", e)
                c = [ta.lo * tb.lo, ta.lo * tb.hi, ta.hi * tb.lo, ta.hi * tb.hi]
                r = self.int_result(IntT(min(c), max(c)), x * y, e)
                return Val(FixT(ta.frac + tb.frac, ta.unit * tb.unit, r.ty.lo, r.ty.hi), r.term)
            self.err("05§5.1", f"no fixed-point '{op}' operation in Core v1", e)
        fx, it = (ta, tb) if isinstance(ta, FixT) else (tb, ta)
        if op == "*" and isinstance(it, IntT):
            c = [fx.lo * it.lo, fx.lo * it.hi, fx.hi * it.lo, fx.hi * it.hi]
            r = self.int_result(IntT(min(c), max(c)), x * y, e)
            return Val(FixT(fx.frac, fx.unit, r.ty.lo, r.ty.hi), r.term)
        self.err("05§5.1", f"'{op}' is not defined between {ta} and {tb} (no implicit conversion)", e)

    def eval_compare(self, e, a: Val, b: Val):
        ta, tb = a.ty, b.ty
        op = e.op
        if isinstance(ta, IntT) and isinstance(tb, IntT):
            pass
        elif isinstance(ta, FixT) and isinstance(tb, FixT):
            self.fix_same_scale(ta, tb, e, "comparison")
        elif isinstance(ta, FloatT) and isinstance(tb, FloatT):
            if ta.bits != tb.bits:
                if _float_literal(e.right) is not None:
                    b = self.retype_literal(e.right, ta.bits)
                elif _float_literal(e.left) is not None:
                    a = self.retype_literal(e.left, tb.bits)
                else:
                    self.err("05§5.1", f"comparison: {ta} and {tb} cannot be mixed", e)
            if not ta.unit.same_unit(tb.unit):
                self.unit_mismatch(tb, ta, e.right, "comparison")
            if ta.frame != tb.frame and not _literal_adopts_frame(e, ta, tb):
                self.err("05§5.2", f"frame mismatch: {ta.frame} and {tb.frame} cannot be compared", e)
        elif isinstance(ta, InstantT) and isinstance(tb, InstantT):
            if ta.clock != tb.clock:
                self.err("03§3.5", f"instants of different clocks cannot be compared: {ta} and {tb}", e)
        elif isinstance(ta, (EnumT, BoolT, PushResT)) and ta == tb and op in ("==", "!="):
            pass
        else:
            self.err("TYPE", f"'{op}' is not defined between {ta} and {tb}", e)
        x, y = a.term, b.term
        if getattr(self, "relaxed", False):
            self.fround_monotone(x, y)
        if self.fp and z3.is_fp(x):
            eq = z3.fpEQ(x, y)
            t = {"==": eq, "!=": z3.Not(eq), "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]
        else:
            t = {"==": x == y, "!=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]
        return Val(BoolT(), t)

    def eval_int_arith(self, e, a: Val, b: Val):
        ta, tb = a.ty, b.ty
        x, y = a.term, b.term
        op = e.op
        if op == "+":
            return self.int_result(IntT(ta.lo + tb.lo, ta.hi + tb.hi), x + y, e)
        if op == "-":
            return self.int_result(IntT(ta.lo - tb.hi, ta.hi - tb.lo), x - y, e)
        if op == "*":
            c = [ta.lo * tb.lo, ta.lo * tb.hi, ta.hi * tb.lo, ta.hi * tb.hi]
            return self.int_result(IntT(min(c), max(c)), x * y, e)
        if op in ("/", "%"):
            self.prove(y != 0, e.right, "R-501", "the divisor may be zero",
                       "For every division it must be proved that the divisor is not zero.")
            q = _tdiv(x, y)
            M = max(abs(ta.lo), abs(ta.hi))
            if op == "/":
                if tb.lo > 0 or tb.hi < 0:
                    c = [_pytdiv(p, r) for p in (ta.lo, ta.hi) for r in (tb.lo, tb.hi)]
                    ty = IntT(min(c), max(c))
                else:
                    ty = IntT(-M, M)
                return self.int_result(ty, q, e)
            B = max(abs(tb.lo), abs(tb.hi))
            lo = 0 if ta.lo >= 0 else -(min(M, B - 1))
            hi = 0 if ta.hi <= 0 else min(M, B - 1)
            return self.int_result(IntT(lo, hi), x - y * q, e)
        self.err("TYPE", f"'{op}' is not defined on integers", e)

    def eval_float_arith(self, e, a: Val, b: Val):
        ta, tb = a.ty, b.ty
        x, y = a.term, b.term
        op = e.op
        if op in ("+", "-"):
            if not ta.unit.same_unit(tb.unit):
                self.unit_mismatch(tb, ta, e.right, f"'{op}'")
            if ta.frame != tb.frame and not _literal_adopts_frame(e, ta, tb):
                self.err("05§5.2", f"frame mismatch: {ta.frame or '(no frame)'} and "
                                   f"{tb.frame or '(no frame)'} cannot be added", e,
                         "A frame change must be written explicitly with a rotation: rotate<Target>(R, v).")
            lo = hi = None
            if ta.lo is not None and tb.lo is not None:
                lo, hi = (ta.lo + tb.lo, ta.hi + tb.hi) if op == "+" else (ta.lo - tb.hi, ta.hi - tb.lo)
            if ta.bits == 32 and lo is not None:
                lo, hi = _f32_bounds(lo, hi, exact=True)
            return Val(FloatT(ta.unit, ta.frame or tb.frame, lo, hi, ta.bits),
                       self.fround(x + y if op == "+" else x - y, lo, hi, ta.bits))
        if op in ("*", "/"):
            if ta.frame and tb.frame:
                self.err("05§5.2", "two framed quantities cannot be multiplied or divided (vector operations are Core v1)", e)
            frame = ta.frame or tb.frame
            if op == "/":
                self.prove(z3.Not(z3.fpIsZero(y)) if self.fp else y != 0, e.right, "R-501",
                           "the floating-point divisor may be zero (in IEEE, −0.0 is also zero)")
            unit = ta.unit * tb.unit if op == "*" else ta.unit / tb.unit
            lo = hi = None
            if ta.lo is not None and tb.lo is not None:
                if op == "*":
                    c = [ta.lo * tb.lo, ta.lo * tb.hi, ta.hi * tb.lo, ta.hi * tb.hi]
                    lo, hi = min(c), max(c)
                elif tb.lo > 0 or tb.hi < 0:
                    c = [ta.lo / tb.lo, ta.lo / tb.hi, ta.hi / tb.lo, ta.hi / tb.hi]
                    lo, hi = min(c), max(c)
            if ta.bits == 32 and lo is not None:
                lo, hi = _f32_bounds(lo, hi, exact=(op == "*"))
            return Val(FloatT(unit, frame, lo, hi, ta.bits), self.fround(x * y if op == "*" else x / y, lo, hi, ta.bits))
        self.err("TYPE", f"'{op}' is not defined on floating point", e)

    def eval_instant_arith(self, e, a: Val, b: Val):
        ta, tb = a.ty, b.ty
        if e.op == "-" and isinstance(ta, InstantT) and isinstance(tb, InstantT):
            if ta.clock != tb.clock:
                self.err("03§3.5", f"epoch mix: {ta} - {tb} is undefined", e,
                         "Instants of different clocks cannot be mixed; the conversion must be explicit.\n"
                         "Deep Impact (2013) [T]: a time-tag problem led to the loss of the spacecraft.")
            return Val(FloatT(U.base("s")), self.fround(a.term - b.term))
        if e.op in ("+", "-") and isinstance(ta, InstantT) and isinstance(tb, FloatT):
            if not tb.unit.same_unit(U.base("s")):
                self.err("03§3.5", f"only a duration in seconds can be added to an instant, {tb.unit} given", e)
            return Val(ta, self.fround(a.term + b.term if e.op == "+" else a.term - b.term))
        self.err("03§3.5", f"'{e.op}' is not defined between {ta} and {tb}", e)

    def eval_call(self, e: A.Call, env, expected):
        if e.func == "print":
            if len(e.args) != 1:
                self.err("TYPE", "print takes one argument", e)
            v = self.eval(e.args[0], env)
            self.no_value_check(v, e.args[0])
            if not isinstance(v.ty, (IntT, FloatT, BoolT, FixT)):
                self.err("TYPE", f"print cannot print {v.ty}", e)
            e._callee = "print"
            return Val(UnitT(), None)
        if e.func == "empty":
            if not isinstance(expected, BoundedT):
                self.err("TYPE", "empty() can be used only in the declaration of a Bounded whose type is known", e)
            e._callee = "empty"
            return Val(expected, None)
        if e.func == "push":
            if len(e.args) != 2 or not isinstance(e.args[0], A.Name):
                self.err("TYPE", "must be called as push(queue_variable, value)", e)
            b = env.get(e.args[0].name)
            if b is None or not isinstance(b.ty, BoundedT):
                self.err("TYPE", "the first argument of push must be a Bounded variable", e)
            if not b.mutable:
                self.err("TYPE", f"'{e.args[0].name}' cannot be changed (it must be var)", e)
            e.args[0]._ty = b.ty
            e.args[0]._ref = "local"
            v = self.eval(e.args[1], env, b.ty.elem)
            self.no_value_check(v, e.args[1])
            self.coerce(v, b.ty.elem, e.args[1], "value pushed")
            e._callee = "push"
            return Val(PushResT(), self.sym(PushResT(), "_push"))
        if e.func == "saturate":
            return self.eval_saturate(e, env)
        if e.func == "decode":
            if len(e.args) != 1:
                self.err("TYPE", "decode takes one argument", e)
            x = self.eval(e.args[0], env)
            self.no_value_check(x, e.args[0])
            if not isinstance(x.ty, WireT):
                self.err("R-202", f"decode expects a Wire<...> value, {x.ty} given", e)
            enc = self.encodings[x.ty.name]
            e._callee = "decode"
            e._enc = x.ty.name
            return Val(enc["sem"], z3.substitute(enc["dec"], (enc["w"], x.term)))
        if e.func in ("take", "put"):
            return self.eval_take_put(e, env)
        if e.func in ("held", "latch"):
            self.err("TYPE", f"{e.func}() can be used only in the initialiser of a state declaration", e)
        if e.func in ("refresh", "read", "feed", "is_set", "reset"):
            return self.eval_state_builtin(e, env)
        if e.func in ("xor", "band", "bor", "shl", "shr"):
            return self.eval_bitop(e, env)
        if e.func == "bits":
            if len(e.args) != 3 or not isinstance(e.args[1], A.IntLit) or not isinstance(e.args[2], A.IntLit):
                self.err("TYPE", "bits(x, start, width): start and width must be constant integers", e)
            x = self.eval(e.args[0], env)
            self.no_value_check(x, e.args[0])
            if not isinstance(x.ty, IntT) or x.ty.lo < 0:
                self.err("TYPE", f"bits is defined only on non-negative integers; {x.ty} given", e)
            start, width = e.args[1].value, e.args[2].value
            if not (start >= 0 and width >= 1 and start + width <= 62):
                self.err("TYPE", "bits: requires 0 ≤ start, 1 ≤ width, start + width ≤ 62", e)
            e.args[1]._ty = IntT(start, start)
            e.args[2]._ty = IntT(width, width)
            e._callee = "bits"
            hi = min(2 ** width - 1, x.ty.hi >> start)
            return Val(IntT(0, hi), (x.term / (2 ** start)) % (2 ** width))
        if e.func in SUSPECT_TAGS:
            self.err("TYPE", f"'{e.func}' can be used only as a match pattern in Core v0; "
                            "suspect values come from the extern (sensor) boundary", e)
        if e.func not in self.fns:
            self.err("NAME", f"undefined function '{e.func}'", e)
        callee = self.sigs.get(e.func)
        if callee is None:
            callee = self.make_sig(self.fns[e.func])
            self.sigs[e.func] = callee
        e._callee = "extern" if callee.fn.is_extern else "user"
        caller = self.cur
        if caller is not None and not caller.modes <= callee.modes:
            extra = sorted(caller.modes - callee.modes)
            self.err("05§5.5", f"mode gate: '{e.func}' can be called only in modes {sorted(callee.modes)}; "
                               f"'{caller.fn.name}' can also be reached from modes {extra}", e,
                     "Ariane 5 Flight 501: an alignment function needed only before lift-off was still running "
                     "about 40 s after lift-off.")
        if len(e.args) != len(callee.params):
            self.err("TYPE", f"'{e.func}' expects {len(callee.params)} arguments, {len(e.args)} given", e)
        subst = []
        for (pname, pty, psym), arg in zip(callee.params, e.args):
            v = self.eval(arg, env, pty)
            self.no_value_check(v, arg)
            v = self.coerce(v, pty, arg, f"parameter '{pname}' of '{e.func}'")
            if psym is not None and v.term is not None and not isinstance(psym, tuple):
                subst.append((psym, v.term))
        for term, node in callee.requires:
            self.prove(z3.substitute(term, *subst) if subst else term, e, "06§6.3",
                       f"the precondition (requires) of '{e.func}' could not be proved at the call site",
                       f"precondition: line {node.line}")
        for term, node in callee.valid_for:
            self.prove(z3.substitute(term, *subst) if subst else term, e, "R-502",
                       f"validation envelope violated: in this calling context '{e.func}' can be used outside the region it was validated for",
                       f"envelope predicate: line {node.line}"
                       + (f"\nvalidated in the context: {callee.validated_by}" if callee.validated_by else "")
                       + "\nAriane 5 Flight 501: SRI code validated within the Ariane 4 envelope was reused unchanged on "
                         "Ariane 5, whose horizontal velocity was much higher.")
        if not callee.fn.is_extern:
            for sname in self.state_writes(e.func):
                b = env.get(sname)
                if b is not None and b.kind == "state":
                    env[sname] = Binding(b.ty, self.sym(b.ty, f"{sname}@{e.func}(...)after"), True, "state")
        if isinstance(callee.ret, UnitT):
            return Val(UnitT(), None)
        r = self.sym(callee.ret, f"{e.func}(...)")
        if callee.ensures and callee.result_sym is not None and not isinstance(r, tuple):
            for term, _ in callee.ensures:
                self.facts.append(z3.substitute(term, *(subst + [(callee.result_sym, r)])))
        return Val(callee.ret, r)

    def eval_bitop(self, e: A.Call, env):
        f = e.func
        if f in ("shl", "shr"):
            if len(e.args) != 2 or not isinstance(e.args[1], A.IntLit) or not (0 <= e.args[1].value <= 62):
                self.err("TYPE", f"{f}(x, n): n must be a constant integer in 0..62", e)
            x = self.eval(e.args[0], env)
            self.no_value_check(x, e.args[0])
            if not isinstance(x.ty, IntT) or x.ty.lo < 0:
                self.err("TYPE", f"{f} is defined only on non-negative integers; {x.ty} given", e)
            n = e.args[1].value
            e.args[1]._ty = IntT(n, n)
            e._callee = f
            if f == "shl":
                ty = IntT(x.ty.lo << n, x.ty.hi << n)
                if not fits_int64(ty):
                    self.err("R-501", f"shl: the result may exceed the int64 range ({ty})", e)
                return Val(ty, x.term * (2 ** n), x.taint)
            return Val(IntT(x.ty.lo >> n, x.ty.hi >> n), x.term / (2 ** n), x.taint)
        if len(e.args) != 2:
            self.err("TYPE", f"{f}(a, b) takes two arguments", e)
        a = self.eval(e.args[0], env)
        b = self.eval(e.args[1], env)
        self.no_value_check(a, e.args[0])
        self.no_value_check(b, e.args[1])
        for v, node in ((a, e.args[0]), (b, e.args[1])):
            if not isinstance(v.ty, IntT) or v.ty.lo < 0:
                self.err("TYPE", f"{f} is defined only on non-negative integers; {v.ty} given", node)
        k = max(1, max(a.ty.hi, b.ty.hi).bit_length())
        if k > 62:
            self.err("TYPE", f"{f}: operands cannot be wider than 62 bits", e)
        e._callee = f
        ba, bb = z3.Int2BV(a.term, k), z3.Int2BV(b.term, k)
        if f == "band":
            term, ty = z3.BV2Int(ba & bb), IntT(0, min(a.ty.hi, b.ty.hi))
        elif f == "bor":
            term, ty = z3.BV2Int(ba | bb), IntT(max(a.ty.lo, b.ty.lo), 2 ** k - 1)
        else:
            term, ty = z3.BV2Int(ba ^ bb), IntT(0, 2 ** k - 1)
        r = self.sym(ty, f"{f}(...)")
        self.facts.append(r == term)
        return Val(ty, r, a.taint or b.taint)

    def eval_index(self, e: A.Index, env):
        base = self.eval(e.base, env)
        self.no_value_check(base, e.base)
        if not isinstance(base.ty, ArrayT):
            self.err("TYPE", f"{base.ty} cannot be indexed", e)
        self.index_obligation(e, base.ty, env)
        return Val(base.ty.elem, self.sym(base.ty.elem, "_array_element"))

    def index_obligation(self, e: A.Index, aty: ArrayT, env):
        i = self.eval(e.index, env)
        self.no_value_check(i, e.index)
        if not isinstance(i.ty, IntT):
            self.err("TYPE", f"an array index must be an integer, found {i.ty}", e.index)
        if i.ty.lo >= 0 and i.ty.hi < aty.n:
            self._record("02§2.5", e.index, f"array bound [0, {aty.n}): index type {i.ty}", "range",
                         goal=z3.And(i.term >= 0, i.term < aty.n) if _is_term(i.term) else None)
            return
        self.prove(z3.And(i.term >= 0, i.term < aty.n), e.index, "02§2.5",
                   f"array bound: the index may be outside [0, {aty.n})",
                   "Buffer over-read from trusting a length field or external data, the most common memory "
                   "error in space software.")

    def eval_narrow(self, e: A.Narrow, env):
        target = self.resolve(e.target)
        v = self.eval(e.expr, env)
        self.no_value_check(v, e.expr)
        if isinstance(target, IntT) and isinstance(v.ty, IntT):
            self.prove(z3.And(v.term >= target.lo, v.term <= target.hi), e, "R-501",
                       f"narrowing could not be proved: the value may be outside the range of {target}")
            return Val(target, v.term)
        if isinstance(target, IntT) and isinstance(v.ty, FloatT):
            if not v.ty.unit.is_dimensionless():
                self.err("05§5.2", f"a value with a unit ({v.ty.unit}) cannot be narrowed to an integer; make it dimensionless first", e)
            self.prove(z3.And(v.term >= target.lo, v.term <= target.hi), e, "R-501",
                       f"narrowing could not be proved: the floating-point value may be outside the range of {target}",
                       "Ariane 5 Flight 501 (1996): the horizontal bias (BH) was converted from a 64-bit float to a 16-bit "
                       "integer without protection and overflowed.\nFix: declare the range of the value in its type "
                       "(Float<1, lo..=hi>) or limit it before the conversion.")
            return Val(target, self.sym(target, "_narrowed"))
        if isinstance(target, FloatT) and isinstance(v.ty, FloatT):
            if target.bits != v.ty.bits:
                if not target.unit.same_unit(v.ty.unit) or target.frame != v.ty.frame:
                    self.err("05§5.2", f"narrowing cannot change the unit or frame: {v.ty} → {target}", e)
                if self.fp:
                    term = z3.fpToFP(z3.RNE(), v.term, self.fsort_of(target.bits))
                else:
                    term = self.fround(v.term, v.ty.lo, v.ty.hi, min(target.bits, v.ty.bits))
                e._conv_bits = target.bits
                w = Val(FloatT(target.unit, target.frame, v.ty.lo, v.ty.hi, target.bits), term, v.taint)
                return self.coerce(w, target, e, "narrowing")
            return self.coerce(v, target, e, "narrowing")
        self.err("TYPE", f"narrowing {v.ty} → {target} is not defined", e)

    def eval_prot_call(self, e: A.ProtCall, env):
        qn = f"{e.obj}.{e.method}"
        if e.obj not in self.protected:
            self.err("NAME", f"unknown protected object '{e.obj}'", e)
        if qn not in self.sigs:
            self.err("NAME", f"object '{e.obj}' has no operation '{e.method}'", e)
        callee = self.sigs[qn]
        caller = self.cur
        if caller is not None and not caller.modes <= callee.modes:
            self.err("05§5.5", f"mode gate: '{qn}' can be called only in modes {sorted(callee.modes)}", e)
        if len(e.args) != len(callee.params):
            self.err("TYPE", f"'{qn}' expects {len(callee.params)} arguments, {len(e.args)} given", e)
        subst = []
        for (pname, pty, psym), arg in zip(callee.params, e.args):
            v = self.eval(arg, env, pty)
            self.no_value_check(v, arg)
            v = self.coerce(v, pty, arg, f"parameter '{pname}' of '{qn}'")
            if psym is not None and v.term is not None and not isinstance(psym, tuple):
                subst.append((psym, v.term))
        for term, node in callee.requires:
            self.prove(z3.substitute(term, *subst) if subst else term, e, "06§6.3",
                       f"the precondition of '{qn}' could not be proved at the call site")
        if isinstance(callee.ret, UnitT):
            return Val(UnitT(), None)
        return Val(callee.ret, self.sym(callee.ret, f"{qn}(...)"))

    def check_rotation(self, d: A.RotationDecl):
        if d.name in self.rotations or d.name in RESERVED:
            self.err("NAME", f"'{d.name}' is defined twice or is a reserved name", d)
        m = d.m
        if len(m) != 3 or any(len(r) != 3 for r in m):
            self.err("05§5.2", f"rotation '{d.name}': a 3×3 matrix is expected", d)
        if d.src == d.dst:
            self.err("05§5.2", f"rotation '{d.name}': source and target frame are the same ({d.src})", d)
        for i in range(3):
            for j in range(3):
                dot = sum(m[i][k] * m[j][k] for k in range(3))
                if abs(dot - (1.0 if i == j else 0.0)) > 1e-6:
                    self.err("05§5.2", f"rotation '{d.name}': the rows are not orthonormal (row {i + 1}·row "
                                       f"{j + 1} = {dot:.9g}); this is not a rotation", d,
                             "A frame transformation preserves lengths and angles. A scale, skew or calibration "
                             "factor is a separate step and must be written with its unit.")
        det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
               - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
               + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
        if det < 0:
            self.err("05§5.2", f"rotation '{d.name}': determinant −1, this is a reflection (it changes handedness)", d,
                     "There is no reflection between two right-handed frames; the sign correction of a sensor mounted "
                     "with the opposite (left) handedness must not be buried silently in a rotation (C-02 class error).")
        self.rotations[d.name] = d

    def eval_rotate(self, e: A.GenericCall, env):
        if len(e.args) != 2 or not isinstance(e.args[0], A.Name) or e.args[0].name not in self.rotations:
            self.err("TYPE", "must be called as rotate<Target>(rotation, record); the first argument is a rotation name", e)
        rot = self.rotations[e.args[0].name]
        e.args[0]._ref = "rotation"
        if e.tname not in self.structs:
            self.err("NAME", f"unknown record type '{e.tname}'", e)
        src = self.eval(e.args[1], env)
        self.no_value_check(src, e.args[1])
        if not isinstance(src.ty, RecordT):
            self.err("TYPE", f"rotate expects a record (a vector with 3 components), {src.ty} given", e.args[1])
        sf = self.structs[src.ty.name]["fields"]
        tf = self.structs[e.tname]["fields"]
        bits = set()
        for side, fields, frame in (("source", sf, rot.src), ("target", tf, rot.dst)):
            if len(fields) != 3:
                self.err("TYPE", f"rotate: the {side} record must have exactly 3 fields", e)
            for f, fty in fields:
                if not isinstance(fty, FloatT):
                    self.err("TYPE", f"rotate: {side} field '{f}' must be floating point, found {fty}", e)
                if fty.frame != frame:
                    self.err("05§5.2", f"rotate {rot.name}: {side} field '{f}' is in frame {fty.frame or '(no frame)'}"
                                       f"; {rot.name} is the transformation {rot.src} -> {rot.dst}", e,
                             "The transformation has a direction: using its inverse spreads the measurement over the wrong axes.")
                bits.add(fty.bits)
        if len(bits) != 1:
            self.err("05§5.1", "rotate: source and target fields must have the same precision", e)
        n = self._rot_n
        self._rot_n += 1
        tmps = [(f"__rot{n}_{f}", f) for f, _ in sf]
        env2 = dict(env)
        for (tmp, f), (_, fty) in zip(tmps, sf):
            env2[tmp] = Binding(FloatT(fty.unit, rot.dst, fty.lo, fty.hi, fty.bits), src.term[f], False,
                                "local", src.taint)
        fields = []
        for i, (fname, _) in enumerate(tf):
            acc = None
            for j, (tmp, _) in enumerate(tmps):
                c = rot.m[i][j]
                if c == 0:
                    continue
                nm = A.Name(tmp, line=e.line, col=e.col)
                if c == 1:
                    term = nm
                elif c == -1:
                    term = A.Unary("-", nm, line=e.line, col=e.col)
                else:
                    term = A.Binary("*", A.FloatLit(float(c), U.DIMENSIONLESS, line=e.line, col=e.col), nm,
                                    line=e.line, col=e.col)
                acc = term if acc is None else A.Binary("+", acc, term, line=e.line, col=e.col)
            fields.append((fname, acc))
        lit = A.StructLit(e.tname, fields, line=e.line, col=e.col)
        v = self.eval(lit, env2, RecordT(e.tname))
        e._rot = (tmps, lit, src.ty)
        return Val(RecordT(e.tname), v.term, src.taint)

    def eval_wire_builtin(self, e: A.GenericCall, env):
        if e.func == "transit":
            return self.eval_transit(e, env)
        if e.func == "rotate":
            return self.eval_rotate(e, env)
        if e.tname not in self.encodings:
            self.err("NAME", f"unknown encoding '{e.tname}'", e)
        enc = self.encodings[e.tname]
        if len(e.args) != 1:
            self.err("TYPE", f"{e.func}<{e.tname}> takes one argument", e)
        x = self.eval(e.args[0], env)
        self.no_value_check(x, e.args[0])
        if e.func == "wire":
            x = self.coerce(x, enc["wire"], e.args[0], f"wire<{e.tname}>")
            return Val(WireT(e.tname), x.term)
        x = self.coerce(x, enc["sem"], e.args[0], f"encode<{e.tname}>")
        return Val(WireT(e.tname), z3.substitute(enc["enc"], (enc["s"], x.term)))

    def eval_saturate(self, e: A.Call, env):
        if len(e.args) != 3:
            self.err("TYPE", "saturate(value, low, high) takes three arguments", e)
        x = self.eval(e.args[0], env)
        self.no_value_check(x, e.args[0])
        lo = self.eval(e.args[1], env, x.ty)
        hi = self.eval(e.args[2], env, x.ty)
        if isinstance(x.ty, FloatT):
            for b, n in ((lo, e.args[1]), (hi, e.args[2])):
                if not isinstance(b.ty, FloatT) or not b.ty.unit.same_unit(x.ty.unit):
                    self.err("05§5.2", f"the saturate bound must be in unit {x.ty.unit}", n)
            if lo.ty.lo is None or hi.ty.lo is None:
                self.err("R-407", "saturate bounds must be constants", e)
            inner = FloatT(x.ty.unit, x.ty.frame, lo.ty.lo, hi.ty.hi, x.ty.bits)
        elif isinstance(x.ty, IntT):
            if not (isinstance(lo.ty, IntT) and isinstance(hi.ty, IntT)):
                self.err("TYPE", "saturate bounds must be integers", e)
            inner = IntT(lo.ty.lo, hi.ty.hi)
        else:
            self.err("TYPE", f"saturate is not defined on {x.ty}", e)
        self.prove(lo.term <= hi.term, e, "R-407", "saturate: the lower bound may be greater than the upper bound")
        e._callee = "saturate"
        ty = ClampedT(inner)
        self.clamp_types.add(ty)
        tag = z3.If(z3.And(x.term >= lo.term, x.term <= hi.term), 0, 1)
        val = z3.If(x.term < lo.term, lo.term, z3.If(x.term > hi.term, hi.term, x.term))
        return Val(ty, (tag, val))

    def eval_state_builtin(self, e: A.Call, env):
        want = {"refresh": 3, "read": 2, "feed": 2, "is_set": 1, "reset": 1}[e.func]
        if len(e.args) != want or not isinstance(e.args[0], A.Name):
            self.err("TYPE", f"{e.func} takes {want} arguments; the first must be a Held/Latch variable", e)
        h = e.args[0]
        b = env.get(h.name)
        if b is None:
            self.err("NAME", f"undefined '{h.name}'", h)
        need = HeldT if e.func in ("refresh", "read") else LatchT
        if not isinstance(b.ty, need):
            self.err("TYPE", f"{e.func}: '{h.name}' must be {need.__name__[:-1]}, found {b.ty}", h)
        h._ty, h._ref = b.ty, ("state" if b.kind == "state" else "local")
        e._callee = e.func
        mono = InstantT("Monotonic")
        if e.func == "refresh":
            v = self.eval(e.args[1], env, b.ty.inner)
            self.no_value_check(v, e.args[1])
            self.coerce(v, b.ty.inner, e.args[1], "refresh value")
            t = self.eval(e.args[2], env, mono)
            self.coerce(t, mono, e.args[2], "refresh time (age is measured on the Monotonic clock)")
            return Val(UnitT(), None)
        if e.func == "read":
            t = self.eval(e.args[1], env, mono)
            self.coerce(t, mono, e.args[1], "read time (age is measured on the Monotonic clock)")
            ty = HeldReadT(b.ty.inner)
            return Val(ty, self.sym(ty, f"read({h.name})"), True)
        if e.func == "feed":
            c = self.eval(e.args[1], env)
            self.expect_bool(c, e.args[1], "feed condition")
            return Val(UnitT(), None)
        if e.func == "is_set":
            return Val(BoolT(), self.sym(BoolT(), f"is_set({h.name})"), False)
        return Val(UnitT(), None)

    def eval_requantize(self, e: A.Requantize, env):
        target = self.resolve(e.target)
        if not isinstance(target, FixT):
            self.err("TYPE", f"the requantize target must be a Fix type, {target} given", e)
        src = e.expr
        neg = isinstance(src, A.Unary) and src.op == "-" and isinstance(src.operand, (A.FloatLit, A.IntLit))
        if isinstance(src, (A.FloatLit, A.IntLit)) or neg:
            lit = src.operand if neg else src
            val = (-1 if neg else 1) * float(lit.value)
            unit = lit.unit if isinstance(lit, A.FloatLit) else U.DIMENSIONLESS
            if not (unit.same_unit(target.unit) or (unit.is_dimensionless() and target.unit.is_dimensionless())):
                if unit.is_dimensionless():
                    self.err("05§5.2", f"requantize: constant without a unit; the unit {target.unit} must be written", e)
                self.err("05§5.2", f"requantize: unit mismatch {unit} → {target.unit}", e)
            exact = val * (2 ** target.frac)
            if exact == int(exact):
                raw = int(exact)
            elif e.rounding is None:
                self.err("05§5.1", f"the constant {val} cannot be represented exactly with fraction {target.frac}; a rounding mode is required", e)
            else:
                raw = _round_py_real(exact, e.rounding)
            if not (target.lo <= raw <= target.hi):
                self.err("R-501", f"requantize: the constant {val} is outside the range of {target}", e)
            e._const_raw = raw
            e._d = None
            src._ty = target
            return Val(target, z3.IntVal(raw))
        x = self.eval(src, env)
        self.no_value_check(x, src)
        if not isinstance(x.ty, FixT):
            self.err("TYPE", f"requantize expects a Fix value (or a constant), found {x.ty}", e)
        if not x.ty.unit.same_unit(target.unit):
            self.err("05§5.2", f"requantize: unit mismatch {x.ty.unit} → {target.unit}", e)
        d = x.ty.frac - target.frac
        e._d = d
        e._const_raw = None
        if d > 0:
            if e.rounding is None:
                self.err("05§5.1", f"a rounding mode is required for a lossy rescaling (fraction {x.ty.frac} → {target.frac})"
                                   "", e,
                         "In fixed point the rounding direction is a source of limit cycles and the decision belongs "
                         "to the engineer; there is no default.\n"
                         "expected: requantize<...>(x, rounding = nearest_even | toward_zero | floor | ceil)")
            self.rounding_used.add(e.rounding)
            p = 2 ** d
            term = _round_z3(x.term, p, e.rounding)
            lo, hi = _round_py_int(x.ty.lo, p, e.rounding), _round_py_int(x.ty.hi, p, e.rounding)
        elif d < 0:
            p = 2 ** (-d)
            term = x.term * p
            lo, hi = x.ty.lo * p, x.ty.hi * p
        else:
            term, lo, hi = x.term, x.ty.lo, x.ty.hi
        r = self.int_result(IntT(lo, hi), term, e)
        v = Val(FixT(target.frac, target.unit, r.ty.lo, r.ty.hi), r.term)
        return self.coerce(v, target, e, "requantize result")

    def eval_convert(self, e: A.Convert, env):
        v = self.eval(e.expr, env)
        self.no_value_check(v, e.expr)
        if not isinstance(v.ty, FloatT):
            self.err("TYPE", f"convert is defined only on floats with a unit, found {v.ty}", e)
        if not v.ty.unit.same_dimension(e.unit):
            self.err("05§5.2", f"convert: {v.ty.unit} → {e.unit} have different dimensions", e)
        f = v.ty.unit.scale / e.unit.scale
        e._factor = f
        t = v.ty
        fe = self.f32(f) if t.bits == 32 else f
        lo = None if t.lo is None else min(t.lo * fe, t.hi * fe)
        hi = None if t.lo is None else max(t.lo * fe, t.hi * fe)
        if t.bits == 32 and lo is not None:
            lo, hi = _f32_bounds(lo, hi, exact=True)
        return Val(FloatT(e.unit, t.frame, lo, hi, t.bits), self.fround(v.term * self.fval(f, t.bits), lo, hi, t.bits))

    def match_arms(self, e: A.Match, env):
        s = self.eval(e.scrutinee, env)
        self.no_value_check(s, e.scrutinee)
        sty = s.ty
        if isinstance(sty, EnumT):
            universe = {v: i for i, v in enumerate(self.enums[sty.name])}
            kind, tag = "enum", s.term
        elif isinstance(sty, SuspectT):
            universe, kind, tag = dict(SUSPECT_TAGS), "suspect", s.term[0]
        elif isinstance(sty, PushResT):
            universe, kind, tag = dict(PUSH_TAGS), "push", s.term
        elif isinstance(sty, HeldReadT):
            universe, kind, tag = dict(HELD_TAGS), "held", s.term[0]
        elif isinstance(sty, ClampedT):
            universe, kind, tag = dict(CLAMP_TAGS), "clamp", s.term[0]
        elif isinstance(sty, ResT):
            sc = e.scrutinee
            if not (isinstance(sc, A.Call) and sc.func == "take"):
                self.err("R-203", "resource states can be matched only on take(state_variable)", e)
            order = self.resources[sty.name]["decl"].states
            universe = {st: order.index(st) for st in order if st in sty.states}
            kind = "res"
            tag = self.sym(IntT(0, len(order) - 1), f"{sty.name}.state")
            self.facts.append(z3.Or(*[tag == i for i in universe.values()]))
        else:
            self.err("TYPE", f"cannot match on {sty} (enum, Suspect, HeldRead, PushResult)", e)
        e._kind = kind
        e._scrut_taint = s.taint
        covered, wild = set(), False
        for arm in e.arms:
            p = arm.pattern
            if p.kind == "wild":
                if wild:
                    self.err("TYPE", "more than one '_' arm", arm)
                wild = True
                continue
            if kind == "res" and p.kind == "wild":
                self.err("R-203", "a resource match cannot have a '_' arm: every state is handled by name "
                                  "(a resource cannot be dropped without being bound)", arm)
            if p.kind != kind:
                self.err("TYPE", f"pattern kind mismatch: pattern '{p.variant}' on {sty}", arm)
            if kind == "enum" and p.enum != sty.name:
                self.err("TYPE", f"pattern '{p.enum}::{p.variant}' is not of type {sty.name}", arm)
            if p.variant not in universe:
                self.err("NAME", f"no variant '{p.variant}'", arm)
            if p.variant in covered:
                self.err("TYPE", f"the arm '{p.variant}' is written twice", arm)
            if kind == "suspect":
                if p.variant == "Invalid" and p.bind:
                    self.err("TYPE", "Invalid carries no value", arm)
                if p.variant != "Invalid" and not p.bind:
                    self.err("TYPE", f"bind the value as {p.variant}(name)", arm)
            if kind == "res" and not p.bind:
                self.err("R-203", f"bind the resource as {p.variant}(name)", arm)
            if kind == "held":
                if p.variant == "Expired" and p.bind:
                    self.err("TYPE", "Expired carries no value; an expired value cannot be used (R-403)", arm)
                if p.variant == "Fresh" and not p.bind:
                    self.err("TYPE", "bind the value as Fresh(name)", arm)
            covered.add(p.variant)
            p._tag = universe[p.variant]
        missing = [v for v in universe if v not in covered]
        if missing and not wild:
            rule = {"suspect": "R-402", "push": "JPL-7", "held": "R-403", "clamp": "R-407",
                    "res": "R-203"}.get(kind, "TYPE")
            self.err(rule, f"incomplete match: arms {missing} are not handled", e,
                     "A sealed match must be exhaustive; there is no `default`.")
        out = []
        for arm in e.arms:
            p = arm.pattern
            if p.kind == "wild":
                cond = z3.And(*[tag != universe[v] for v in covered]) if covered else z3.BoolVal(True)
            else:
                cond = tag == p._tag
            bind = None
            if kind in ("suspect", "held") and p.kind != "wild" and p.bind:
                bind = (p.bind, Binding(sty.inner, s.term[1], False, taint=True), arm)
            elif kind == "clamp" and p.kind != "wild" and p.bind:
                bind = (p.bind, Binding(sty.inner, s.term[1], False, taint=s.taint), arm)
            elif kind == "res" and p.kind != "wild":
                rty = ResT(sty.name, frozenset({p.variant}))
                bind = (p.bind, Binding(rty, self.sym(rty, p.bind), False, "local"), arm)
                arm._bind_ty = rty
            out.append((cond, arm.body, bind))
        return out

    def join(self, results, node, expected):
        tys = [v.ty for _, v, _ in results]
        first = tys[0]
        if all(isinstance(t, IntT) for t in tys):
            ty = IntT(min(t.lo for t in tys), max(t.hi for t in tys))
        elif all(isinstance(t, FixT) for t in tys):
            for t in tys[1:]:
                self.fix_same_scale(first, t, node, "arms")
            ty = FixT(first.frac, first.unit, min(t.lo for t in tys), max(t.hi for t in tys))
        elif all(isinstance(t, FloatT) for t in tys):
            for t in tys[1:]:
                if not t.unit.same_unit(first.unit) or t.frame != first.frame:
                    self.err("05§5.2", f"the arms produce different units or frames: {first} and {t}", node)
            if all(t.lo is not None for t in tys):
                ty = FloatT(first.unit, first.frame, min(t.lo for t in tys), max(t.hi for t in tys), first.bits)
            else:
                ty = FloatT(first.unit, first.frame, bits=first.bits)
        elif all(structurally_equal(t, first) for t in tys):
            ty = first
        else:
            self.err("TYPE", f"the arms produce different types: {', '.join(map(str, tys))}", node)
        term = results[-1][1].term
        for cond, v, _ in reversed(results[:-1]):
            term = _ite(cond, v.term, term)
        return Val(ty, term)

    def coerce(self, v: Val, target, node, what: str) -> Val:
        src = v.ty
        if isinstance(target, (ResT, BorrowT)) or isinstance(src, (ResT, BorrowT)):
            return self.coerce_resource(v, target, node, what)
        if isinstance(src, WireT) and not isinstance(target, WireT):
            enc = self.encodings[src.name]["decl"]
            self.err("R-202", f"{what}: an encoded wire value ({src}) cannot be used in place of {target}", node,
                     f"The wire representation differs from the semantic value (declaration: line {enc.line}). A hand-written conversion "
                     "is forgotten somewhere, and when it is forgotten it is silent.\nCCSDS 133.0-B-2: the Packet Data Length field "
                     "is ONE LESS than the number of octets in the data field.\nexpected: decode(x)")
        if isinstance(src, ClampedT) and not isinstance(target, ClampedT):
            self.err("R-407", f"{what}: a saturated command ({src}) cannot be used in place of {target}", node,
                     "Saturation loses information in both directions: if the command was clamped, the controller (integrator) "
                     "must know it, otherwise the integrator winds up and the vehicle overshoots "
                     "badly.\nexpected: match c { Unclamped(v) => { integrator runs; v }, "
                     "Clamped(v) => { integrator frozen; v } }")
        if isinstance(src, SuspectT) and not isinstance(target, SuspectT):
            self.err("R-402", f"{what}: a suspect value ({src}) cannot be used in place of {target}", node,
                     "A measurement that may be saturated, stale or invalid cannot enter a derived state without explicit handling.\n"
                     "Schiaparelli (2016): a saturated IMU measurement flowed into the attitude estimate and the vehicle was lost.\n"
                     "expected: match x { Valid(v) => ..., Saturated(v) => ..., Stale(v) => ..., Invalid => ... }")
        if isinstance(target, IntT):
            if not isinstance(src, IntT):
                if isinstance(src, FloatT):
                    self.err("00§0.3", f"{what}: no implicit conversion, {src} → {target}; use narrow<...>(...)",
                             node, "Ariane 5 Flight 501 class.")
                self.err("TYPE", f"{what}: expected {target}, found {src}", node)
            if not (target.lo <= src.lo and src.hi <= target.hi):
                self.prove(z3.And(v.term >= target.lo, v.term <= target.hi), node, "R-501",
                           f"{what}: the value may be outside the range of {target} (overflow could not be ruled out)",
                           "Integer overflow does not wrap silently; it is either proved impossible or handled explicitly.\n"
                           "Boeing 787 GCU (2015): a 248-day counter overflow could have cut all AC power.")
            else:
                self._record("R-501", node, f"{what}: {src} ⊆ {target}", "range",
                             goal=z3.And(v.term >= target.lo, v.term <= target.hi) if _is_term(v.term) else None)
            return Val(target, v.term, v.taint)
        if isinstance(target, FloatT):
            if not isinstance(src, FloatT):
                self.err("TYPE", f"{what}: expected {target}, found {src}", node)
            if src.bits != target.bits:
                lit = _float_literal(node)
                if lit is None:
                    self.err("05§5.1", f"{what}: no implicit precision conversion, {src} → {target}; "
                                       f"use narrow<{target}>(...)", node,
                             "Converting binary64 ↔ binary32 involves rounding and must be written explicitly.")
                v = self.retype_literal(node, target.bits)
                src = v.ty
            if not src.unit.same_unit(target.unit):
                self.unit_mismatch(src, target, node, what)
            if src.frame != target.frame and not (src.frame is None and _float_literal(node) is not None):
                self.err("05§5.2", f"{what}: frame mismatch, {src.frame or '(no frame)'} given, "
                                   f"{target.frame or '(no frame)'} expected", node,
                         "A frame change must be written explicitly with a rotation: rotate<Target>(R, v).")
            if target.lo is not None:
                if src.lo is not None and target.lo <= src.lo and src.hi <= target.hi:
                    self._record("R-501", node, f"{what}: {src} ⊆ {target}", "range",
                                 goal=z3.And(v.term >= target.lo, v.term <= target.hi) if _is_term(v.term) else None)
                else:
                    self.prove(z3.And(v.term >= target.lo, v.term <= target.hi), node, "R-501",
                               f"{what}: the value may be outside the range [{target.lo}, {target.hi}]")
            return Val(target, v.term, v.taint)
        if isinstance(target, FixT):
            if not isinstance(src, FixT):
                self.err("05§5.1", f"{what}: expected {target}, found {src} (no implicit conversion; use requantize)",
                         node)
            self.fix_same_scale(src, target, node, what)
            if target.lo <= src.lo and src.hi <= target.hi:
                self._record("R-501", node, f"{what}: {src} ⊆ {target}", "range",
                             goal=z3.And(v.term >= target.lo, v.term <= target.hi) if _is_term(v.term) else None)
            else:
                self.prove(z3.And(v.term >= target.lo, v.term <= target.hi), node, "R-501",
                           f"{what}: the raw fixed-point value may be outside the range of {target}")
            return Val(target, v.term, v.taint)
        if isinstance(target, InstantT):
            if not isinstance(src, InstantT):
                self.err("TYPE", f"{what}: expected {target}, found {src}", node)
            if src.clock != target.clock:
                self.err("03§3.5", f"{what}: epoch mix, {src} given, {target} expected", node,
                         "Deep Impact (2013) [T]: a time-tag problem.")
            return Val(target, v.term, v.taint)
        if isinstance(target, SuspectT):
            if not isinstance(src, SuspectT) or not structurally_equal(src, target):
                self.err("TYPE", f"{what}: expected {target}, found {src}", node)
            return Val(target, v.term, v.taint)
        if not structurally_equal(src, target):
            self.err("TYPE", f"{what}: expected {target}, found {src}", node)
        return Val(target, v.term, v.taint)

def _fmt(v) -> str:
    if z3.is_fp_value(v):
        if v.isNaN():
            return "NaN"
        if v.isInf():
            return "-inf" if v.isNegative() else "inf"
        r = z3.simplify(z3.fpToReal(v))
        return f"{r.numerator_as_long() / r.denominator_as_long():.17g}"
    if z3.is_rational_value(v):
        num, den = v.numerator_as_long(), v.denominator_as_long()
        return str(num) if den == 1 else f"{num / den:.6g}"
    return str(v)

def _const_of(term):
    if z3.is_fp_value(term):
        r = z3.simplify(z3.fpToReal(term))
        return r.numerator_as_long() / r.denominator_as_long()
    if z3.is_int_value(term):
        return term.as_long()
    if z3.is_rational_value(term):
        return term.numerator_as_long() / term.denominator_as_long()
    raise ValueError(term)

def _f32_bounds(lo: float, hi: float, exact: bool):
    import math
    import struct

    def f32(v):
        try:
            return struct.unpack("f", struct.pack("f", v))[0]
        except OverflowError:
            return math.copysign(math.inf, v)

    def step(v, up):
        b = struct.unpack("<i", struct.pack("<f", v))[0]
        if v == 0.0:
            return struct.unpack("<f", struct.pack("<i", 1 if up else -2147483647))[0]
        b += 1 if (v > 0) == up else -1
        return struct.unpack("<f", struct.pack("<i", b))[0]
    if exact:
        return f32(lo), f32(hi)
    lo2, hi2 = f32(math.nextafter(lo, -math.inf)), f32(math.nextafter(hi, math.inf))
    return step(lo2, False), step(hi2, True)

def _float_literal(node):
    if isinstance(node, A.FloatLit):
        return node
    if isinstance(node, A.Unary) and node.op == "-" and isinstance(node.operand, A.FloatLit):
        return node.operand
    return None

def _literal_adopts_frame(e, ta, tb) -> bool:
    return (ta.frame is None and _float_literal(e.left) is not None) or \
        (tb.frame is None and _float_literal(e.right) is not None)

def _is_term(t) -> bool:
    return isinstance(t, z3.ExprRef)

def env_or_empty(ck):
    return getattr(ck, "_path_env", {}) or {}

def _ite(c, a, b):
    if a is None or b is None:
        return a
    if isinstance(a, dict):
        return {k: _ite(c, a[k], b[k]) for k in a}
    if isinstance(a, tuple):
        return tuple(_ite(c, x, y) for x, y in zip(a, b))
    if a.eq(b):
        return a
    return z3.If(c, a, b)

def _tdiv(x, y):
    ax = z3.If(x >= 0, x, -x)
    ay = z3.If(y >= 0, y, -y)
    q0 = ax / ay
    return z3.If((x >= 0) == (y >= 0), q0, -q0)

def _pytdiv(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q

def _walk(node):
    if node is None:
        return
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
        return
    if isinstance(node, tuple):
        for x in node:
            yield from _walk(x)
        return
    if not isinstance(node, A.Node):
        return
    yield node
    for f in node.__dataclass_fields__:
        if f in ("line", "col"):
            continue
        yield from _walk(getattr(node, f))

def _assigned_names(body) -> set:
    out = set()
    for n in _walk(body):
        if isinstance(n, A.Assign):
            t = n.target
            if isinstance(t, A.Index):
                t = t.base
            out.add(t.name if isinstance(t, A.Name) else t.left)
        elif isinstance(n, A.Call) and n.func in ("push", "refresh", "feed", "reset") and n.args \
                and isinstance(n.args[0], A.Name):
            out.add(n.args[0].name)
    return out

def _expr_children(e):
    if isinstance(e, A.Unary):
        return [e.operand]
    if isinstance(e, A.Binary):
        return [e.left, e.right]
    if isinstance(e, A.Call):
        if e.func in ("is_set",):
            return []
        return list(e.args) + list(e.kwargs.values())
    if isinstance(e, A.Index):
        return [e.base, e.index]
    if isinstance(e, (A.Narrow, A.Convert, A.Requantize)):
        return [e.expr]
    if isinstance(e, A.GenericCall):
        return list(e.args)
    if isinstance(e, A.StructLit):
        return [x for _, x in e.fields]
    if isinstance(e, A.ArrayRepeat):
        return [e.value]
    if isinstance(e, A.Match):
        out = [e.scrutinee]
        for arm in e.arms:
            out.append(arm.body.tail if isinstance(arm.body, A.Block) else arm.body)
        return [x for x in out if x is not None]
    if isinstance(e, A.IfExpr):
        out = [e.cond, e.then.tail]
        if isinstance(e.els, A.Block):
            out.append(e.els.tail)
        elif e.els is not None:
            out.append(e.els)
        return [x for x in out if x is not None]
    if isinstance(e, A.Block):
        return [e.tail] if e.tail is not None else []
    return []

def _round_py_int(x: int, p: int, mode: str) -> int:
    q = x // p
    r = x - q * p
    if mode == "floor":
        return q
    if mode == "ceil":
        return q + (1 if r else 0)
    if mode == "toward_zero":
        return q if (x >= 0 or r == 0) else q + 1
    if 2 * r > p or (2 * r == p and q % 2 != 0):
        return q + 1
    return q

def _round_py_real(v: float, mode: str) -> int:
    import math
    if mode == "floor":
        return math.floor(v)
    if mode == "ceil":
        return math.ceil(v)
    if mode == "toward_zero":
        return int(v)
    return round(v)

def _round_z3(x, p: int, mode: str):
    q = x / p
    r = x - q * p
    if mode == "floor":
        return q
    if mode == "ceil":
        return z3.If(r == 0, q, q + 1)
    if mode == "toward_zero":
        return z3.If(z3.Or(x >= 0, r == 0), q, q + 1)
    return z3.If(2 * r > p, q + 1, z3.If(2 * r < p, q, z3.If(q % 2 == 0, q, q + 1)))

def check(prog: A.Program, filename: str, float_model: str = "ieee") -> Checker:
    return Checker(prog, filename, float_model).check()
