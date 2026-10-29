import math

from . import ast as A
from .checker import Checker, _round_py_int
from .stypes import (INT64_MAX, INT64_MIN, ArrayT, BoolT, BorrowT, BoundedT, ClampedT, EnumT, FixT, FloatT,
                     HeldReadT, HeldT, InstantT, IntT, LatchT, RecordT, ResT, SuspectT, UnitT, WireT)

class Res:
    __slots__ = ("store", "state", "gen")

    def __init__(self, store, state, gen=0):
        self.store, self.state, self.gen = store, state, gen

    def __repr__(self):
        return f"Res({self.state}, generation {self.gen})"

class Violation(Exception):

    def __init__(self, kind: str, msg: str, node=None):
        self.kind = kind
        self.msg = msg
        self.line = getattr(node, "line", 0)
        super().__init__(f"[{kind}] line {self.line}: {msg}")

class HaltSignal(Exception):
    def __init__(self, why):
        self.why = why

class PreconditionFalse(Exception):
    pass

class _Return(Exception):
    def __init__(self, value):
        self.value = value

def _f32(v: float) -> float:
    import struct
    try:
        return struct.unpack("f", struct.pack("f", v))[0]
    except OverflowError:
        return math.copysign(math.inf, v)

def _tdiv(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q

def zero_of(ty, ck):
    if isinstance(ty, (IntT, FixT, EnumT, WireT)):
        return 0
    if isinstance(ty, (FloatT, InstantT)):
        return 0.0
    if isinstance(ty, BoolT):
        return False
    if isinstance(ty, (SuspectT, HeldReadT, ClampedT)):
        return (0, zero_of(ty.inner, ck))
    if isinstance(ty, ArrayT):
        return [zero_of(ty.elem, ck) for _ in range(ty.n)]
    if isinstance(ty, RecordT):
        return {f: zero_of(t, ck) for f, t in ck.structs[ty.name]["fields"]}
    if isinstance(ty, ResT):
        return Res(None, ty.single() or ck.resources[ty.name]["states"][0])
    return None

class Interp:
    def __init__(self, ck: Checker, externs: dict | None = None, check_floats: bool = True):
        self.ck = ck
        self.externs = externs or {}
        self.check_floats = check_floats
        self.out: list[str] = []
        self.steps = 0
        self.max_steps = 5_000_000
        self.float_notes: list[str] = []
        self.consts = {}
        self.cfg = {}
        for it in ck.prog.items:
            if isinstance(it, A.ConstDecl):
                self.consts[it.name] = it._cval
            elif isinstance(it, A.ConfigDecl):
                for f in it.fields:
                    self.cfg[(it.name, f.name)] = f._cval
        self.states = {}
        for name, info in ck.states.items():
            if info["kind"] == "held":
                self.states[name] = {"val": info["init"], "t": 0.0, "max_age": info["max_age_s"], "valid": False}
            elif info["kind"] == "latch":
                self.states[name] = {"count": 0, "need": info["need"], "set": False}
            elif info["kind"] == "resource":
                rinfo = ck.resources[info["ty"].name]
                store = ({"gen": 0, **{f: self._copy(v) for f, v in info["init"].items()}}
                         if rinfo["fields"] else None)
                self.states[name] = Res(store, rinfo["states"][0])
            else:
                self.states[name] = self._copy(info["init"])
        self.pfields = {p: {f: fi["init"] for f, fi in pi["fields"].items()} for p, pi in ck.protected.items()}

    def oracle(self, v, ty, node, where="expression"):
        if isinstance(ty, FixT):
            ty = ty.as_int()
        if isinstance(ty, IntT):
            if not isinstance(v, int) or isinstance(v, bool):
                raise Violation("type", f"{where}: expected {ty}, produced {v!r}", node)
            if not (ty.lo <= v <= ty.hi):
                raise Violation("integer", f"{where}: value {v} outside its static type {ty}", node)
            if not (INT64_MIN <= v <= INT64_MAX):
                raise Violation("integer", f"{where}: value {v} outside int64 (undefined behaviour in C)", node)
        elif isinstance(ty, FloatT):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                raise Violation("float", f"{where}: produced {v}", node)
            lo, hi = (ty.lo, ty.hi) if ty.bits == 64 or ty.lo is None else (_f32(ty.lo), _f32(ty.hi))
            if self.check_floats and ty.lo is not None and not (lo <= v <= hi):
                raise Violation("float", f"{where}: value {v!r} outside its static range [{ty.lo}, {ty.hi}] "
                                         "(real-number model ≠ IEEE-754)", node)
        elif isinstance(ty, EnumT):
            if not (0 <= v < len(self.ck.enums[ty.name])):
                raise Violation("enum", f"{where}: tag {v} outside {ty.name}", node)
        elif isinstance(ty, (SuspectT, HeldReadT, ClampedT)):
            self.oracle(v[1], ty.inner, node, where + ".value")
        elif isinstance(ty, RecordT):
            for f, fty in self.ck.structs[ty.name]["fields"]:
                self.oracle(v[f], fty, node, f"{where}.{f}")
        elif isinstance(ty, ArrayT) and isinstance(ty.elem, (IntT, FixT)):
            for x in v:
                self.oracle(x, ty.elem, node, where + "[]")
        elif isinstance(ty, (ResT, BorrowT)):
            rty = ty.inner if isinstance(ty, BorrowT) else ty
            if not isinstance(v, Res):
                raise Violation("type", f"{where}: expected {ty}, produced {v!r}", node)
            if v.state not in rty.states:
                raise Violation("state", f"{where}: resource is in state '{v.state}' at run time, static type {ty}", node)
            self.fresh(v, node, where)
        return v

    def fresh(self, r, node, where="resource"):
        if r.store is not None and r.store["gen"] != r.gen:
            raise Violation("linearity", f"{where}: handle from an old generation (move after use): "
                                           f"handle {r.gen}, storage {r.store['gen']}", node)

    @staticmethod
    def _copy(v):
        if isinstance(v, list):
            return [Interp._copy(x) for x in v]
        if isinstance(v, dict):
            return {k: Interp._copy(x) for k, x in v.items()}
        return v

    def tick(self, node):
        self.steps += 1
        if self.steps > self.max_steps:
            raise Violation("step", "step budget exceeded (non-terminating execution?)", node)

    def run_main(self):
        if "main" not in self.ck.fns:
            raise ValueError("no main")
        return self.call("main", [])

    def call(self, name: str, args: list, node=None):
        fn = self.ck.fns[name]
        sig = self.ck.sigs[name]
        for (pname, pty, _), a in zip(sig.params, args):
            self.oracle(a, pty, node, f"parameter '{pname}' of '{name}'")
        if fn.is_extern:
            impl = self.externs.get(name)
            r = impl(*args) if impl is not None else zero_of(sig.ret, self.ck)
            if not isinstance(sig.ret, UnitT):
                self.oracle(r, sig.ret, node, f"return of extern '{name}' (R-701 boundary)")
            return r
        env = {pname: self._copy(a) for (pname, _, _), a in zip(sig.params, args)}
        for r in list(fn.requires) + [x for an in fn.annotations if an.name == "valid_for"
                                      for x in an.args if not isinstance(x, tuple)]:
            if not self.expr(r, dict(env)):
                if node is None:
                    raise PreconditionFalse(name)
                raise Violation("contract", f"precondition / validation envelope of '{name}' does not hold at the call site "
                                           "(it was claimed proved)", node)
        prot = getattr(fn, "_prot", None)
        saved = getattr(self, "_prot", None)
        self._prot = prot
        try:
            v = self.block(fn.body, env)
        except _Return as r:
            v = r.value
        finally:
            self._prot = saved
        if not isinstance(sig.ret, UnitT):
            self.oracle(v, sig.ret, fn, f"return value of '{name}'")
            for en in fn.ensures:
                env["result"] = v
                if not self.expr(en, dict(env)):
                    raise Violation("contract", f"postcondition of '{name}' is false at run time (it was claimed "
                                               f"proved); result = {v!r}", en)
        return v

    def block(self, b: A.Block, env: dict):
        local = dict(env)
        for st in b.stmts:
            self.stmt(st, local)
        for k in env:
            env[k] = local[k]
        if b.tail is not None:
            v = self.expr(b.tail, local)
            for k in env:
                env[k] = local[k]
            return v
        return None

    def assign_name(self, tgt: A.Name, v, env):
        ref = getattr(tgt, "_ref", "local")
        if ref == "state":
            self.states[tgt.name] = v
        elif ref == "pfield":
            self.pfields[self._prot][tgt.name] = v
        else:
            env[tgt.name] = v

    def read_name(self, e: A.Name, env):
        ref = getattr(e, "_ref", "local")
        if ref == "state":
            return self.states[e.name]
        if ref == "pfield":
            return self.pfields[self._prot][e.name]
        if ref == "const":
            return self.consts[e.name]
        return env[e.name]

    def stmt(self, st, env):
        self.tick(st)
        if isinstance(st, (A.Let, A.Var)):
            v = self._copy(self.expr(st.value, env))
            env[st.name] = self.oracle(v, st._ty, st, f"'{st.name}'")
            return
        if isinstance(st, A.Assign):
            v = self._copy(self.expr(st.value, env))
            tgt = st.target
            if isinstance(tgt, A.Name):
                self.oracle(v, tgt._ty, st, f"assignment to '{tgt.name}'")
                self.assign_name(tgt, v, env)
                return
            if isinstance(tgt, A.Path) or (isinstance(tgt, A.Index) and isinstance(tgt.base, A.Path)):
                path = tgt if isinstance(tgt, A.Path) else tgt.base
                r = env[path.left]
                self.fresh(r, st)
                self.oracle(v, tgt._ty, st, f"assignment to '{path.left}.{path.right}'")
                if isinstance(tgt, A.Path):
                    r.store[path.right] = v
                else:
                    i = self.expr(tgt.index, env)
                    arr = r.store[path.right]
                    if not (0 <= i < len(arr)):
                        raise Violation("array", f"index {i}, array length {len(arr)}", st)
                    arr[i] = v
                return
            i = self.expr(tgt.index, env)
            base = self.read_name(tgt.base, env)
            if not (0 <= i < len(base)):
                raise Violation("array", f"index {i}, array length {len(base)}", st)
            self.oracle(v, tgt._ty, st, "array element assignment")
            base[i] = v
            return
        if isinstance(st, A.Return):
            raise _Return(None if st.value is None else self.expr(st.value, env))
        if isinstance(st, A.Halt):
            raise HaltSignal(st.because)
        if isinstance(st, A.For):
            for i in range(st.lo, st.hi):
                env2 = dict(env)
                env2[st.var] = i
                self.block(st.body, env2)
                for k in env:
                    env[k] = env2[k]
            return
        if isinstance(st, A.ExprStmt):
            self.expr(st.expr, env)
            return
        raise NotImplementedError(type(st).__name__)

    def expr(self, e, env):
        v = self._expr(e, env)
        ty = getattr(e, "_ty", None)
        if isinstance(ty, FloatT) and ty.bits == 32 and isinstance(v, float):
            v = _f32(v)
        if ty is not None and v is not None and not isinstance(ty, UnitT):
            self.oracle(v, ty, e)
        return v

    def _expr(self, e, env):
        self.tick(e)
        if isinstance(e, (A.IntLit, A.FloatLit, A.BoolLit)):
            return e.value
        if isinstance(e, A.Name):
            return self.read_name(e, env)
        if isinstance(e, A.Path):
            ref = e._ref
            if ref[0] == "enum":
                return ref[2]
            if ref[0] == "field":
                base = self.states[ref[1]] if ref[3] == "state" else env[ref[1]]
                return base[ref[2]]
            if ref[0] == "rfield":
                r = env[ref[1]]
                self.fresh(r, e)
                return r.store[ref[2]]
            return self.cfg[(ref[1], ref[2])]
        if isinstance(e, A.Unary):
            x = self.expr(e.operand, env)
            return (not x) if e.op == "!" else -x
        if isinstance(e, A.Binary):
            return self.binary(e, env)
        if isinstance(e, A.Call):
            return self.builtin_or_call(e, env)
        if isinstance(e, A.Index):
            base = self.expr(e.base, env)
            i = self.expr(e.index, env)
            if not (0 <= i < len(base)):
                raise Violation("array", f"index {i}, array length {len(base)}", e)
            return base[i]
        if isinstance(e, A.ArrayRepeat):
            v = self.expr(e.value, env)
            return [self._copy(v) for _ in range(e.n)]
        if isinstance(e, A.Narrow):
            x = self.expr(e.expr, env)
            if isinstance(e._ty, IntT) and isinstance(x, float):
                if math.isnan(x) or math.isinf(x):
                    raise Violation("float", f"narrowed value {x}", e)
                return int(x)
            return x
        if isinstance(e, A.Convert):
            return self.expr(e.expr, env) * e._factor
        if isinstance(e, A.Requantize):
            if e._const_raw is not None:
                return e._const_raw
            x = self.expr(e.expr, env)
            if e._d > 0:
                return _round_py_int(x, 1 << e._d, e.rounding)
            if e._d < 0:
                return x * (1 << (-e._d))
            return x
        if isinstance(e, A.StructLit):
            return {f: self._copy(self.expr(x, env)) for f, x in e.fields}
        if isinstance(e, A.ProtCall):
            args = [self.expr(a, env) for a in e.args]
            return self.call(f"{e.obj}.{e.method}", args, e)
        if isinstance(e, A.GenericCall) and e.func == "rotate":
            tmps, lit, _ = e._rot
            src = self.expr(e.args[1], env)
            env2 = dict(env)
            for tmp, f in tmps:
                env2[tmp] = src[f]
            return self.expr(lit, env2)
        if isinstance(e, A.GenericCall) and e.func == "transit":
            x = self.expr(e.args[0], env)
            for w in e.args[1:]:
                self.expr(w, env)
            rn, a, b, _ = e._trans
            if x.state != a:
                raise Violation("state", f"transit: resource in state '{x.state}', transition {a} -> {b}", e)
            self.fresh(x, e)
            if x.store is None:
                return Res(None, b)
            x.store["gen"] += 1
            return Res(x.store, b, x.store["gen"])
        if isinstance(e, A.GenericCall):
            x = self.expr(e.args[0], env)
            if e.func == "wire":
                return x
            d = self.ck.encodings[e.tname]["decl"]
            return self.expr(d.eexpr, {d.eparam: x})
        if isinstance(e, A.IfExpr):
            c = self.expr(e.cond, env)
            if c:
                return self.block(e.then, env)
            if e.els is None:
                return None
            if isinstance(e.els, A.Block):
                return self.block(e.els, env)
            return self.expr(e.els, env)
        if isinstance(e, A.Match):
            return self.match(e, env)
        if isinstance(e, A.Block):
            return self.block(e, env)
        raise NotImplementedError(type(e).__name__)

    def binary(self, e: A.Binary, env):
        op = e.op
        if op == "&&":
            return bool(self.expr(e.left, env)) and bool(self.expr(e.right, env))
        if op == "||":
            return bool(self.expr(e.left, env)) or bool(self.expr(e.right, env))
        a = self.expr(e.left, env)
        b = self.expr(e.right, env)
        if op in ("==", "!=", "<", "<=", ">", ">="):
            return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        is_int = isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op in ("/", "%"):
            if b == 0:
                raise Violation("division", "division by zero (R-501 proof violated)", e)
            if is_int:
                q = _tdiv(a, b)
                return q if op == "/" else a - b * q
            if op == "/":
                return a / b
        raise NotImplementedError(op)

    def match(self, e: A.Match, env):
        s = self.expr(e.scrutinee, env)
        tag = s[0] if e._kind in ("suspect", "held", "clamp") else s
        if e._kind == "res":
            tag = self.ck.resources[e.scrutinee._ty.name]["states"].index(s.state)
        arms = e.arms
        for idx, arm in enumerate(arms):
            p = arm.pattern
            last = idx == len(arms) - 1
            if p.kind == "wild" or p._tag == tag or last:
                if p.kind != "wild" and p._tag != tag:
                    raise Violation("match", f"no arm matched tag {tag}", e)
                env2 = dict(env)
                if e._kind in ("suspect", "held", "clamp") and p.kind != "wild" and p.bind:
                    env2[p.bind] = s[1]
                if e._kind == "res":
                    env2[p.bind] = s
                if isinstance(arm.body, A.Block):
                    v = self.block(arm.body, env2)
                else:
                    v = self.expr(arm.body, env2)
                for k in env:
                    env[k] = env2[k]
                return v
        raise Violation("match", "empty match", e)

    def builtin_or_call(self, e: A.Call, env):
        k = e._callee
        if k == "take":
            nm = e.args[0].name
            r = self.states[nm]
            if r is None:
                raise Violation("linearity", f"take({nm}): state is empty (taken and not put back)", e)
            self.states[nm] = None
            return r
        if k == "put":
            nm = e.args[0].name
            v = self.expr(e.args[1], env)
            if self.states[nm] is not None:
                raise Violation("linearity", f"put({nm}, …): state is full, the old resource would be dropped", e)
            self.states[nm] = v
            return None
        if k == "print":
            x = self.expr(e.args[0], env)
            ty = e.args[0]._ty
            if isinstance(ty, FixT):
                self.out.append(f"{x / (2.0 ** ty.frac):.6f}")
            elif isinstance(ty, IntT):
                self.out.append(str(x))
            elif isinstance(ty, FloatT):
                self.out.append(f"{x:.6f}")
            else:
                self.out.append("true" if x else "false")
            return None
        if k == "empty":
            return {"data": [], "len": 0}
        if k == "push":
            q = self.read_name(e.args[0], env)
            v = self.expr(e.args[1], env)
            n = e.args[0]._ty.n
            if q["len"] >= n:
                return 1
            q["data"].append(v)
            q["len"] += 1
            return 0
        if k == "saturate":
            x, lo, hi = (self.expr(a, env) for a in e.args)
            if x < lo:
                return (1, lo)
            if x > hi:
                return (1, hi)
            return (0, x)
        if k == "decode":
            w = self.expr(e.args[0], env)
            d = self.ck.encodings[e._enc]["decl"]
            return self.expr(d.dexpr, {d.dparam: w})
        if k in ("refresh", "read", "feed", "is_set", "reset"):
            h = self.read_name(e.args[0], env)
            if k == "refresh":
                h["val"] = self.expr(e.args[1], env)
                h["t"] = self.expr(e.args[2], env)
                h["valid"] = True
                return None
            if k == "read":
                now = self.expr(e.args[1], env)
                fresh = h["valid"] and (now - h["t"]) <= h["max_age"]
                return (0 if fresh else 1, h["val"])
            if k == "feed":
                c = self.expr(e.args[1], env)
                if c:
                    if h["count"] < h["need"]:
                        h["count"] += 1
                    if h["count"] >= h["need"]:
                        h["set"] = True
                else:
                    h["count"] = 0
                return None
            if k == "is_set":
                return h["set"]
            h["count"], h["set"] = 0, False
            return None
        if k in ("xor", "band", "bor"):
            a = self.expr(e.args[0], env)
            b = self.expr(e.args[1], env)
            return {"xor": a ^ b, "band": a & b, "bor": a | b}[k]
        if k in ("shl", "shr"):
            a = self.expr(e.args[0], env)
            n = e.args[1].value
            return a << n if k == "shl" else a >> n
        if k == "bits":
            x = self.expr(e.args[0], env)
            start, width = e.args[1].value, e.args[2].value
            return (x >> start) & ((1 << width) - 1)
        args = [self.expr(a, env) for a in e.args]
        return self.call(e.func, args, e)

def run(ck: Checker, externs=None, check_floats: bool = True):
    it = Interp(ck, externs, check_floats)
    r = it.run_main()
    return it.out, r
