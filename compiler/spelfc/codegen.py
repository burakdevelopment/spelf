from . import ast as A
from .checker import Checker
from .layout import field_ctype
from .stypes import (INT64_MIN, ArrayT, BoolT, BorrowT, BoundedT, ClampedT, EnumT, FixT, FloatT, HeldReadT,
                    HeldT, InstantT, IntT, LatchT, PushResT, RecordT, ResT, SuspectT, UnitT, WireT)

RQ_HELPERS = {
    "floor": """static inline int64_t spelf_rq_floor(int64_t x, int d) {
    const int64_t p = INT64_C(1) << d;
    return x >= 0 ? x / p : -((-x + p - 1) / p);
}""",
    "ceil": """static inline int64_t spelf_rq_ceil(int64_t x, int d) {
    const int64_t p = INT64_C(1) << d;
    return x >= 0 ? (x + p - 1) / p : -((-x) / p);
}""",
    "toward_zero": """static inline int64_t spelf_rq_toward_zero(int64_t x, int d) {
    const int64_t p = INT64_C(1) << d;
    return x / p;
}""",
    "nearest_even": """static inline int64_t spelf_rq_nearest_even(int64_t x, int d) {
    const int64_t p = INT64_C(1) << d;
    int64_t q = x >= 0 ? x / p : -((-x + p - 1) / p);
    const int64_t r = x - q * p;
    if (2 * r > p || (2 * r == p && q % 2 != 0)) { q += 1; }
    return q;
}""",
}

def iv(ty):
    return ty.as_int() if isinstance(ty, FixT) else ty

def _walk(node):
    if node is None:
        return
    if isinstance(node, (list, tuple)):
        for x in node:
            yield from _walk(x)
        return
    if not isinstance(node, A.Node):
        return
    yield node
    for f in node.__dataclass_fields__:
        if f not in ("line", "col"):
            yield from _walk(getattr(node, f))

I32_MIN, I32_MAX = -(2 ** 31), 2 ** 31 - 1

def arith_ctype(t: IntT) -> str:
    return "int32_t" if I32_MIN <= t.lo and t.hi <= I32_MAX else "int64_t"

def storage_ctype(t: IntT) -> str:
    if t.lo >= 0:
        for hi, name in ((255, "uint8_t"), (65535, "uint16_t"), (2 ** 32 - 1, "uint32_t")):
            if t.hi <= hi:
                return name
        return "int64_t"
    for lo, hi, name in ((-128, 127, "int8_t"), (-32768, 32767, "int16_t"), (I32_MIN, I32_MAX, "int32_t")):
        if lo <= t.lo and t.hi <= hi:
            return name
    return "int64_t"

def widest(*tys) -> str:
    return "int64_t" if any(arith_ctype(t) == "int64_t" for t in tys if isinstance(t, IntT)) else "int32_t"

class CGen:
    def __init__(self, ck: Checker, freestanding: bool = False, dma_coherent: bool = False,
                 print_hook: bool = False):
        self.freestanding = freestanding
        self.print_hook = print_hook
        self.dma_coherent = dma_coherent
        self.ck = ck
        self.cur_ret = None
        self.tmp = 0
        self.typedefs: list[str] = []
        self.defined: set[str] = set()
        self.helpers: list[str] = []
        self.ptr_params: set[str] = set()
        self.cur_out = False
        self.nrvo: str | None = None

    def mangle(self, ty, elem: bool = False) -> str:
        if isinstance(ty, FixT):
            ty = ty.as_int()
        if isinstance(ty, HeldT):
            return "H" + self.mangle(ty.inner)
        if isinstance(ty, HeldReadT):
            return "R" + self.mangle(ty.inner)
        if isinstance(ty, ClampedT):
            return "C" + self.mangle(ty.inner)
        if isinstance(ty, RecordT):
            return "Rec" + ty.name
        if isinstance(ty, LatchT):
            return "L"
        if isinstance(ty, IntT):
            return storage_ctype(ty).replace("_t", "").replace("int", "i") if elem else \
                arith_ctype(ty).replace("_t", "").replace("int", "i")
        if isinstance(ty, FloatT) and ty.bits == 32:
            return "f32"
        if isinstance(ty, (FloatT, InstantT)):
            return "f64"
        if isinstance(ty, BoolT):
            return "b"
        if isinstance(ty, (EnumT, PushResT)):
            return "e"
        if isinstance(ty, SuspectT):
            return "S" + self.mangle(ty.inner)
        if isinstance(ty, ArrayT):
            return f"A{ty.n}{self.mangle(ty.elem, elem=True)}"
        if isinstance(ty, BoundedT):
            return f"B{ty.n}{self.mangle(ty.elem, elem=True)}"
        raise ValueError(ty)

    def elem_ctype(self, ty) -> str:
        return storage_ctype(iv(ty)) if isinstance(ty, (IntT, FixT)) else self.ctype(ty)

    def conv(self, c: str, ty) -> str:
        if isinstance(ty, (IntT, FixT)) and c:
            return f"(({arith_ctype(iv(ty))})({c}))"
        return c

    def res_storage(self, name: str) -> bool:
        return bool(self.ck.resources[name]["fields"])

    def ctype(self, ty) -> str:
        if isinstance(ty, ResT):
            return f"R_{ty.name} *" if self.res_storage(ty.name) else "uint8_t"
        if isinstance(ty, BorrowT):
            return f"const R_{ty.inner.name} *" if self.res_storage(ty.inner.name) else "uint8_t"
        if isinstance(ty, WireT):
            return arith_ctype(self.ck.encodings[ty.name]["wire"])
        if isinstance(ty, (IntT, FixT)):
            return arith_ctype(iv(ty))
        if isinstance(ty, FloatT) and ty.bits == 32:
            return "float"
        if isinstance(ty, (FloatT, InstantT)):
            return "double"
        if isinstance(ty, BoolT):
            return "bool"
        if isinstance(ty, (EnumT, PushResT)):
            return "int32_t"
        if isinstance(ty, UnitT):
            return "void"
        name = "T_" + self.mangle(ty)
        if name not in self.defined:
            self.defined.add(name)
            if isinstance(ty, SuspectT):
                inner = self.ctype(ty.inner)
                self.typedefs.append(f"typedef struct {{ int32_t tag; {inner} val; }} {name};")
            elif isinstance(ty, ArrayT):
                elem = self.elem_ctype(ty.elem)
                self.typedefs.append(f"typedef struct {{ {elem} a[{ty.n}]; }} {name};")
            elif isinstance(ty, HeldT):
                inner = self.ctype(ty.inner)
                rname = self.ctype(HeldReadT(ty.inner))
                self.typedefs.append(f"typedef struct {{ {inner} val; double t; double max_age; bool valid; }} {name};")
                self.helpers.append(
                    f"static void held_refresh_{name}({name} *h, {inner} v, double now) {{\n"
                    f"    h->val = v;\n    h->t = now;\n    h->valid = true;\n}}\n"
                    f"static {rname} held_read_{name}(const {name} *h, double now) {{\n"
                    f"    {rname} r;\n    r.val = h->val;\n"
                    f"    r.tag = (h->valid && (now - h->t) <= h->max_age) ? 0 : 1;\n"
                    f"    return r;\n}}")
            elif isinstance(ty, HeldReadT):
                inner = self.ctype(ty.inner)
                self.typedefs.append(f"typedef struct {{ int32_t tag; {inner} val; }} {name};")
            elif isinstance(ty, RecordT):
                fields = self.ck.structs[ty.name]["fields"]
                decls = " ".join(f"{self.ctype(fty)} {f};" for f, fty in fields)
                self.typedefs.append(f"typedef struct {{ {decls} }} {name};")
            elif isinstance(ty, ClampedT):
                inner = self.ctype(ty.inner)
                self.typedefs.append(f"typedef struct {{ int32_t tag; {inner} val; }} {name};")
                self.helpers.append(
                    f"static {name} spelf_sat_{name}({inner} x, {inner} lo, {inner} hi) {{\n"
                    f"    {name} r;\n"
                    f"    if (x < lo) {{ r.tag = 1; r.val = lo; }}\n"
                    f"    else if (x > hi) {{ r.tag = 1; r.val = hi; }}\n"
                    f"    else {{ r.tag = 0; r.val = x; }}\n"
                    f"    return r;\n}}")
            elif isinstance(ty, LatchT):
                self.typedefs.append(f"typedef struct {{ int32_t count; int32_t need; bool set; }} {name};")
                self.helpers.append(
                    f"static void latch_feed({name} *l, bool c) {{\n"
                    f"    if (c) {{\n        if (l->count < l->need) {{ l->count += 1; }}\n"
                    f"        if (l->count >= l->need) {{ l->set = true; }}\n"
                    f"    }} else {{\n        l->count = 0;\n    }}\n}}\n"
                    f"static void latch_reset({name} *l) {{ l->count = 0; l->set = false; }}")
            elif isinstance(ty, BoundedT):
                elem = self.elem_ctype(ty.elem)
                self.typedefs.append(f"typedef struct {{ {elem} data[{ty.n}]; int64_t len; }} {name};")
                self.helpers.append(
                    f"static int32_t push_{name}({name} *q, {elem} v) {{\n"
                    f"    if (q->len >= {ty.n}) {{ return 1; }}\n"
                    f"    q->data[q->len] = v;\n    q->len += 1;\n    return 0;\n}}")
        return name

    @staticmethod
    def int_lit(v: int) -> str:
        if v == INT64_MIN:
            return "INT64_MIN"
        if I32_MIN < v <= I32_MAX:
            return str(v) if v >= 0 else f"({v})"
        return f"INT64_C({v})" if v >= 0 else f"(-INT64_C({-v}))"

    @staticmethod
    def float_lit(v: float) -> str:
        r = repr(float(v))
        if "inf" in r or "nan" in r:
            raise ValueError(r)
        if "." not in r and "e" not in r:
            r += ".0"
        return r

    def ref(self, e) -> str:
        r = getattr(e, "_ref", "local")
        if r == "pfield":
            return f"pf_{e._obj}_{e.name}"
        if r == "local" and e.name in self.ptr_params:
            return f"(*v_{e.name})"
        if r == "local" and e.name == self.nrvo:
            return "(*v__ret)"
        return {"const": "k_", "state": "s_"}.get(r, "v_") + e.name

    @staticmethod
    def by_ptr(ty) -> bool:
        return isinstance(ty, ArrayT)

    def out_ret(self, fn) -> bool:
        if fn.is_extern or getattr(fn, "_prot", None) is not None:
            return False
        return isinstance(self.ck.sigs[fn.name].ret, (ArrayT, RecordT))

    def nrvo_candidate(self, fn):
        b = fn.body
        if b is None or not isinstance(b.tail, A.Name):
            return None
        name = b.tail.name
        if not any(isinstance(st, (A.Let, A.Var)) and st.name == name for st in b.stmts):
            return None
        if any(isinstance(n, A.Return) for n in _walk(b)):
            return None
        return name

    def param_decl(self, n, t) -> str:
        return f"const {self.ctype(t)} *restrict v_{n}" if self.by_ptr(t) else f"{self.ctype(t)} v_{n}"

    def array_arg(self, a, c: str, callee: str | None, pre: list) -> str:
        if isinstance(a, A.Name):
            ref = getattr(a, "_ref", "local")
            aliased = (ref == "state" and callee is not None and a.name in self.ck.state_writes(callee)) \
                or ref == "pfield"
            if not aliased:
                return f"&{self.ref(a)}"
        t = self.new_tmp()
        pre.append(f"{self.ctype(a._ty)} {t} = {c};")
        return f"&{t}"

    @staticmethod
    def cname(fn) -> str:
        if getattr(fn, "_prot", None) is not None:
            return f"po_{fn._prot}_{fn._op}"
        if getattr(fn, "_task", None) is not None:
            return f"{'isr' if getattr(fn, '_isr', False) else 'task'}_{fn._task}"
        return fn.name if fn.is_extern else f"f_{fn.name}"

    def new_tmp(self) -> str:
        self.tmp += 1
        return f"t{self.tmp}"

    def expr(self, e) -> tuple[list, str]:
        if isinstance(e, A.IntLit):
            return [], self.int_lit(e.value)
        if isinstance(e, A.FloatLit):
            if getattr(e, "_bits", 64) == 32:
                return [], self.float_lit(e.value) + "f"
            return [], self.float_lit(e.value)
        if isinstance(e, A.BoolLit):
            return [], "true" if e.value else "false"
        if isinstance(e, A.Name):
            return [], self.ref(e)
        if isinstance(e, A.Path):
            ref = e._ref
            if ref[0] == "rfield":
                return [], f"(v_{ref[1]}->{ref[2]})"
            if ref[0] == "field":
                base = ("s_" if ref[3] == "state" else "v_") + ref[1]
                return [], f"({base}.{ref[2]})"
            if ref[0] == "enum":
                return [], f"E_{ref[1]}_{e.right}"
            return [], f"cfg_{ref[1]}_{ref[2]}"
        if isinstance(e, A.Unary):
            pre, x = self.expr(e.operand)
            if e.op == "-" and isinstance(e._ty, (IntT, FixT)):
                R = widest(iv(e._ty), iv(e.operand._ty))
                return pre, f"(-({R})({x}))"
            return pre, f"({e.op}{x})"
        if isinstance(e, A.Binary):
            pa, a = self.expr(e.left)
            pb, b = self.expr(e.right)
            if e.op in ("&&", "||") and pb:
                t = self.new_tmp()
                guard = t if e.op == "&&" else f"!{t}"
                return pa + [f"bool {t} = {a};", f"if ({guard}) {{"] + ["    " + l for l in pb] + \
                    [f"    {t} = {b};", "}"], t
            lt, rt = iv(getattr(e.left, "_ty", None)), iv(getattr(e.right, "_ty", None))
            if isinstance(lt, IntT) and isinstance(rt, IntT):
                R = widest(lt, rt, iv(e._ty))
                return pa + pb, f"(({R})({a}) {e.op} ({R})({b}))"
            return pa + pb, f"({a} {e.op} {b})"
        if isinstance(e, A.Call):
            return self.call(e)
        if isinstance(e, A.Index):
            pb, b = self.expr(e.base)
            pi, i = self.expr(e.index)
            return pb + pi, f"{b}.a[{i}]"
        if isinstance(e, A.ArrayRepeat):
            pv, v = self.expr(e.value)
            t = self.new_tmp()
            ct = self.ctype(e._ty)
            et = self.elem_ctype(e._ty.elem)
            return pv + [f"{ct} {t};",
                         f"for (int32_t i_{t} = 0; i_{t} < {e.n}; ++i_{t}) {{ {t}.a[i_{t}] = ({et})({v}); }}"], t
        if isinstance(e, A.Narrow):
            pre, x = self.expr(e.expr)
            if getattr(e, "_conv_bits", None) is not None:
                return pre, f"(({'float' if e._conv_bits == 32 else 'double'})({x}))"
            if isinstance(e._ty, IntT) and isinstance(e.expr._ty, FloatT):
                return pre, f"(({arith_ctype(e._ty)})({x}))"
            return pre, x
        if isinstance(e, A.StructLit):
            ftys = dict(self.ck.structs[e.name]["fields"])
            pre, parts = [], []
            for fname, fexpr in e.fields:
                p, c = self.expr(fexpr)
                pre += p
                parts.append(f".{fname} = {self.conv(c, ftys[fname])}")
            return pre, f"(({self.ctype(e._ty)}){{ {', '.join(parts)} }})"
        if isinstance(e, A.ProtCall):
            sig = self.ck.sigs[f"{e.obj}.{e.method}"]
            pre, args = [], []
            for a, (_, pt, _) in zip(e.args, sig.params):
                p, c = self.expr(a)
                pre += p
                args.append(self.array_arg(a, c, None, pre) if self.by_ptr(pt) else self.conv(c, pt))
            return pre, f"po_{e.obj}_{e.method}({', '.join(args)})"
        if isinstance(e, A.GenericCall) and e.func == "transit":
            return self.transit(e)
        if isinstance(e, A.GenericCall) and e.func == "rotate":
            tmps, lit, sty = e._rot
            pre, s = self.expr(e.args[1])
            t = self.new_tmp()
            pre.append(f"{self.ctype(sty)} {t} = {s};")
            ftys = dict(self.ck.structs[sty.name]["fields"])
            for tmp, f in tmps:
                pre.append(f"{self.ctype(ftys[f])} v_{tmp} = {t}.{f};")
            p2, c = self.expr(lit)
            return pre + p2, c
        if isinstance(e, A.GenericCall):
            pre, x = self.expr(e.args[0])
            enc = self.ck.encodings[e.tname]
            if e.func == "wire":
                return pre, f"(({arith_ctype(enc['wire'])})({x}))"
            return pre, f"spelf_encode_{e.tname}({self.conv(x, enc['sem'])})"
        if isinstance(e, A.Requantize):
            R = arith_ctype(iv(e._ty))
            if e._const_raw is not None:
                return [], f"(({R})({self.int_lit(e._const_raw)}))"
            pre, x = self.expr(e.expr)
            if e._d > 0:
                return pre, f"(({R})spelf_rq_{e.rounding}((int64_t)({x}), {e._d}))"
            if e._d < 0:
                return pre, f"(({R})((int64_t)({x}) * INT64_C({1 << (-e._d)})))"
            return pre, f"(({R})({x}))"
        if isinstance(e, A.Convert):
            pre, x = self.expr(e.expr)
            suf = "f" if isinstance(e._ty, FloatT) and e._ty.bits == 32 else ""
            return pre, f"({x} * {self.float_lit(e._factor)}{suf})"
        if isinstance(e, (A.Match, A.IfExpr)):
            t = self.new_tmp()
            lines = [f"{self.ctype(e._ty)} {t};"] + self.branch(e, ("assign", t, e._ty))
            return lines, t
        raise NotImplementedError(type(e).__name__)

    def call(self, e: A.Call):
        kind = e._callee
        if kind == "take":
            return [], f"s_{e.args[0].name}"
        if kind == "put":
            pre, v = self.expr(e.args[1])
            nm = e.args[0].name
            pre.append(f"s_{nm} = {v};")
            ty = self.ck.states[nm]["ty"]
            if len(ty.states) > 1:
                idx = self.ck.resources[ty.name]["states"].index(e._put_state)
                pre.append(f"s_{nm}__st = {idx}u;")
            return pre, ""
        pre, args = [], []
        ptys = [t for _, t, _ in self.ck.sigs[e.func].params] if kind in ("user", "extern") else [None] * len(e.args)
        for a, pt in zip(e.args, ptys):
            p, c = self.expr(a)
            pre += p
            if pt is not None and self.by_ptr(pt):
                args.append(self.array_arg(a, c, e.func if kind == "user" else None, pre))
            else:
                args.append(self.conv(c, pt) if pt is not None else c)
        if kind == "decode":
            return pre, f"spelf_decode_{e._enc}({args[0]})"
        if kind == "saturate":
            cname = self.ctype(e._ty)
            inner = e._ty.inner
            a = [self.conv(x, inner) for x in args]
            return pre, f"spelf_sat_{cname}({a[0]}, {a[1]}, {a[2]})"
        if kind in ("refresh", "read", "feed", "is_set", "reset"):
            h = e.args[0]
            addr = "&" + self.ref(h)
            hname = self.ctype(h._ty)
            if kind == "refresh":
                return pre, f"held_refresh_{hname}({addr}, {self.conv(args[1], h._ty.inner)}, {args[2]})"
            if kind == "read":
                return pre, f"held_read_{hname}({addr}, {args[1]})"
            if kind == "feed":
                return pre, f"latch_feed({addr}, {args[1]})"
            if kind == "is_set":
                return pre, f"({self.ref(h)}.set)"
            return pre, f"latch_reset({addr})"
        if kind == "print" and self.print_hook:
            ty = e.args[0]._ty
            if isinstance(ty, FixT):
                pre.append(f"spelf_print_f64((double)({args[0]}) / {self.float_lit(2.0 ** ty.frac)});")
            elif isinstance(ty, IntT):
                pre.append(f"spelf_print_i64((int64_t)({args[0]}));")
            elif isinstance(ty, FloatT):
                pre.append(f"spelf_print_f64((double)({args[0]}));")
            else:
                pre.append(f"spelf_print_bool(({args[0]}) ? 1 : 0);")
            return pre, ""
        if kind == "print":
            ty = e.args[0]._ty
            if isinstance(ty, FixT):
                pre.append(f'printf("%.6f\\n", (double)({args[0]}) / {self.float_lit(2.0 ** ty.frac)});')
                return pre, ""
            if isinstance(ty, IntT):
                pre.append(f'printf("%" PRId64 "\\n", (int64_t)({args[0]}));')
            elif isinstance(ty, FloatT):
                pre.append(f'printf("%.6f\\n", (double)({args[0]}));')
            else:
                pre.append(f'puts(({args[0]}) ? "true" : "false");')
            return pre, ""
        if kind in ("xor", "band", "bor"):
            R = widest(iv(e.args[0]._ty), iv(e.args[1]._ty), iv(e._ty))
            op = {"xor": "^", "band": "&", "bor": "|"}[kind]
            return pre, f"(({R})({args[0]}) {op} ({R})({args[1]}))"
        if kind in ("shl", "shr"):
            R = widest(iv(e.args[0]._ty), iv(e._ty))
            op = "<<" if kind == "shl" else ">>"
            return pre, f"(({R})({args[0]}) {op} {e.args[1].value})"
        if kind == "bits":
            start, width = e.args[1].value, e.args[2].value
            R = arith_ctype(e._ty)
            return pre, f"(({R})(((int64_t)({args[0]}) >> {start}) & INT64_C({(1 << width) - 1})))"
        if kind == "empty":
            return pre, f"(({self.ctype(e._ty)}){{ .len = 0 }})"
        if kind == "push":
            bty = e.args[0]._ty
            et = self.elem_ctype(bty.elem)
            return pre, f"push_{self.ctype(bty)}(&v_{e.args[0].name}, ({et})({args[1]}))"
        if kind == "extern":
            return pre, f"{e.func}({', '.join(args)})"
        fn = self.ck.fns[e.func]
        if self.out_ret(fn):
            t = self.new_tmp()
            pre.append(f"{self.ctype(e._ty)} {t};")
            pre.append(f"f_{e.func}({', '.join(args + ['&' + t])});")
            return pre, t
        return pre, f"f_{e.func}({', '.join(args)})"

    def transit(self, e):
        pre, x = self.expr(e.args[0])
        if len(e.args) > 1:
            pw, w = self.expr(e.args[1])
            pre += pw + [f"(void)({w});"]
        rn, a, b, obl = e._trans
        t = self.new_tmp()
        pre.append(f"{self.ctype(e._ty)} {t} = {x};")
        for o in obl:
            if o == "barrier":
                pre.append("spelf_mem_barrier();")
            elif not self.dma_coherent:
                fn = "spelf_cache_clean" if o == "clean" else "spelf_cache_invalidate"
                pre.append(f"{fn}((const volatile void *){t}, (uint32_t)sizeof(*{t}));")
        self.uses_cache_ops = self.uses_cache_ops or bool(obl)
        return pre, t

    def resource_decls(self) -> tuple[list, list]:
        ck = self.ck
        types, stores = [], []
        for rn, info in ck.resources.items():
            if not info["fields"]:
                continue
            decls = []
            for f, fty in info["fields"]:
                decls.append(f"{self.ctype(fty) if isinstance(fty, ArrayT) else field_ctype(fty)} {f};")
            types.append(f"typedef struct R_{rn} {{ {' '.join(decls)} }} R_{rn};")
        for sname, info in ck.states.items():
            ty = info["ty"]
            if not isinstance(ty, ResT):
                continue
            rinfo = ck.resources[ty.name]
            multi = len(ty.states) > 1
            if not rinfo["fields"]:
                stores.append(f"static uint8_t s_{sname} = 0u;")
            else:
                attrs = []
                reg = ck.regions.get(rinfo["region"]) if rinfo["region"] else None
                if reg is not None:
                    if self.freestanding:
                        attrs.append(f'section(".spelf.{rinfo["region"]}")')
                    if reg["kind"] == "dma":
                        attrs.append(f"aligned({reg['line']})")
                attr = f" __attribute__(({', '.join(attrs)}))" if attrs else ""
                init = []
                for f, fty in rinfo["fields"]:
                    init.append(f".{f} = {self.scalar_lit(info['init'][f], fty)}")
                stores.append(f"static R_{ty.name} s_{sname}__mem{attr} = {{ {', '.join(init)} }};")
                stores.append(f"static R_{ty.name} *s_{sname} = &s_{sname}__mem;")
            if multi:
                stores.append(f"static uint8_t s_{sname}__st = 0u;")
        return types, stores

    def sink(self, s, c: str) -> list:
        if s[0] == "ret":
            if self.cur_out and c == "(*v__ret)":
                return ["return;"]
            if self.cur_out and c:
                return [f"*v__ret = {c};", "return;"]
            return [f"return {self.conv(c, self.cur_ret)};"] if c else ["return;"]
        if s[0] == "assign":
            return [f"{s[1]} = {self.conv(c, s[2]) if len(s) > 2 else c};"]
        return [f"(void)({c});"] if c else []

    def branch(self, e, s) -> list:
        if isinstance(e, A.IfExpr):
            pre, c = self.expr(e.cond)
            out = pre + [f"if ({c}) {{"] + self.indent(self.block(e.then, s)) + ["}"]
            if e.els is not None:
                inner = self.branch(e.els, s) if isinstance(e.els, A.IfExpr) else self.block(e.els, s)
                out += ["else {"] + self.indent(inner) + ["}"]
            return out
        pre, sc = self.expr(e.scrutinee)
        sty = e.scrutinee._ty
        t = self.new_tmp()
        out = pre + [f"{self.ctype(sty)} {t} = {sc};"]
        tag = f"{t}.tag" if e._kind in ("suspect", "held", "clamp") else t
        if e._kind == "res":
            tag = f"s_{e.scrutinee.args[0].name}__st"
        arms = e.arms
        first = True
        for idx, arm in enumerate(arms):
            p = arm.pattern
            last = idx == len(arms) - 1
            if p.kind == "wild" or last:
                head = "else {" if not first else "{"
            else:
                head = ("if" if first else "else if") + f" ({tag} == {p._tag}) {{"
            body = []
            if p.kind in ("suspect", "held", "clamp") and p.bind:
                body.append(f"{self.ctype(sty.inner)} v_{p.bind} = {self.conv(t + '.val', sty.inner)};")
            if p.kind == "res":
                body.append(f"{self.ctype(arm._bind_ty)} v_{p.bind} = {t};")
            if isinstance(arm.body, A.Block):
                body += self.block(arm.body, s)
            elif isinstance(arm.body, (A.IfExpr, A.Match)):
                body += self.branch(arm.body, s)
            else:
                bp, bc = self.expr(arm.body)
                body += bp
                if s[0] == "discard" and bc == "":
                    pass
                elif getattr(arm.body, "_ty", None) is not None and isinstance(arm.body._ty, UnitT):
                    if bc:
                        body.append(f"{bc};")
                    if s[0] == "ret":
                        body.append("return;")
                else:
                    body += self.sink(s, bc)
            out += [head] + self.indent(body) + ["}"]
            first = False
        return out

    @staticmethod
    def indent(lines: list) -> list:
        return ["    " + l for l in lines]

    def block(self, b: A.Block, s) -> list:
        out = []
        for st in b.stmts:
            out += self.stmt(st)
        if b.tail is not None:
            t = b.tail
            if isinstance(t, (A.IfExpr, A.Match)):
                out += self.branch(t, s)
            else:
                pre, c = self.expr(t)
                out += pre
                if isinstance(getattr(t, "_ty", None), UnitT):
                    if c:
                        out.append(f"{c};")
                    if s[0] == "ret":
                        out.append("return;")
                else:
                    out += self.sink(s, c)
        return out

    def stmt(self, st) -> list:
        if isinstance(st, (A.Let, A.Var)):
            target = "(*v__ret)" if st.name == self.nrvo else f"v_{st.name}"
            decl = "" if st.name == self.nrvo else f"{self.ctype(st._ty)} "
            if isinstance(st.value, A.ArrayRepeat):
                pv, v = self.expr(st.value.value)
                et = self.elem_ctype(st._ty.elem)
                n = st.value.n
                lines = pv + ([f"{decl.strip()} {target};"] if decl else [])
                lines.append(f"for (int32_t i_{st.name} = 0; i_{st.name} < {n}; ++i_{st.name}) "
                             f"{{ {target}.a[i_{st.name}] = ({et})({v}); }}")
                return lines
            pre, c = self.expr(st.value)
            if st.name == self.nrvo:
                return pre + [f"(*v__ret) = {self.conv(c, st._ty)};"]
            return pre + [f"{self.ctype(st._ty)} v_{st.name} = {self.conv(c, st._ty)};"]
        if isinstance(st, A.Assign):
            pre, c = self.expr(st.value)
            tg = st.target
            if isinstance(tg, A.Path) or (isinstance(tg, A.Index) and isinstance(tg.base, A.Path)):
                path = tg if isinstance(tg, A.Path) else tg.base
                lhs = f"v_{path.left}->{path.right}"
                if isinstance(tg, A.Index):
                    pi, i = self.expr(tg.index)
                    return pre + pi + [f"{lhs}.a[{i}] = ({field_ctype(tg._ty)})({c});"]
                return pre + [f"{lhs} = ({field_ctype(tg._ty)})({c});"]
            if isinstance(st.target, A.Name):
                return pre + [f"{self.ref(st.target)} = {self.conv(c, st.target._ty)};"]
            pi, i = self.expr(st.target.index)
            et = self.elem_ctype(st.target._ty)
            return pre + pi + [f"{self.ref(st.target.base)}.a[{i}] = ({et})({c});"]
        if isinstance(st, A.Return):
            if st.value is None:
                return ["return;"]
            pre, c = self.expr(st.value)
            if self.cur_out:
                return pre + [f"*v__ret = {c};", "return;"]
            return pre + [f"return {self.conv(c, self.cur_ret)};"]
        if isinstance(st, A.Halt):
            why = (st.because or "").replace("\\", "\\\\").replace('"', '\\"')
            return [f'spelf_halt("{why}");']
        if isinstance(st, A.For):
            it = arith_ctype(IntT(st.lo, st.hi))
            return [f"for ({it} v_{st.var} = {st.lo}; v_{st.var} < {st.hi}; "
                    f"++v_{st.var}) {{"] + self.indent(self.block(st.body, ("discard",))) + ["}"]
        if isinstance(st, A.ExprStmt):
            e = st.expr
            if isinstance(e, (A.IfExpr, A.Match)):
                return self.branch(e, ("discard",))
            pre, c = self.expr(e)
            return pre + ([f"{c};"] if c else [])
        raise NotImplementedError(type(st).__name__)

    def scalar_lit(self, v, ty) -> str:
        if isinstance(ty, ArrayT):
            return "{ .a = { " + ", ".join(self.scalar_lit(x, ty.elem) for x in v) + " } }"
        if isinstance(ty, RecordT):
            fields = self.ck.structs[ty.name]["fields"]
            return "{ " + ", ".join(f".{f} = {self.scalar_lit(v[f], fty)}" for f, fty in fields) + " }"
        if isinstance(ty, BoolT):
            return "true" if v else "false"
        if isinstance(ty, (IntT, FixT, EnumT)):
            return self.int_lit(int(v))
        return self.float_lit(v)

    def signature(self, fn: A.FnDecl) -> str:
        sig = self.ck.sigs[fn.name]
        ps = [self.param_decl(n, t) for n, t, _ in sig.params]
        if self.out_ret(fn):
            ps.append(f"{self.ctype(sig.ret)} *restrict v__ret")
            return f"void {self.cname(fn)}({', '.join(ps)})"
        params = ", ".join(ps) or "void"
        ret = self.ctype(sig.ret)
        return f"{ret} {self.cname(fn)}({params})"

    def generate(self) -> str:
        ck = self.ck
        body: list[str] = []
        for n in _walk(ck.prog):
            ty = getattr(n, "_ty", None)
            if ty is None or isinstance(ty, UnitT):
                continue
            try:
                self.ctype(ty)
            except ValueError:
                pass
        for sig in ck.sigs.values():
            for _, t, _ in sig.params:
                self.ctype(t)
            self.ctype(sig.ret)

        header = [
            "#include <stdint.h>", "#include <stdbool.h>",
        ] + ([] if self.freestanding or self.print_hook else ["#include <stdio.h>", "#include <inttypes.h>"]) +             (["extern void spelf_print_i64(int64_t v);", "extern void spelf_print_f64(double v);",
              "extern void spelf_print_bool(int b);"] if self.print_hook else []) + [""]
        enums = []
        for en, vs in ck.enums.items():
            for i, v in enumerate(vs):
                enums.append(f"#define E_{en}_{v} {i}")
        consts = []
        for it in ck.prog.items:
            if isinstance(it, A.ConstDecl):
                c = self.int_lit(it._cval) if isinstance(it._ty, IntT) else self.float_lit(it._cval)
                consts.append(f"static const {self.ctype(it._ty)} k_{it.name} = {c};")
            elif isinstance(it, A.ConfigDecl):
                for f in it.fields:
                    c = self.int_lit(f._cval) if isinstance(f._ty, IntT) else self.float_lit(f._cval)
                    consts.append(f"static const {self.ctype(f._ty)} cfg_{it.name}_{f.name} = {c};")
        states = []
        self.uses_cache_ops = getattr(self, "uses_cache_ops", False)
        res_types, res_stores = self.resource_decls() if ck.resources else ([], [])
        states += res_stores
        for sname, info in ck.states.items():
            ty = info["ty"]
            if info["kind"] == "resource":
                continue
            ct = self.ctype(ty)
            if info["kind"] == "held":
                states.append(f"static {ct} s_{sname} = {{ .val = {self.scalar_lit(info['init'], ty.inner)}, "
                              f".t = 0.0, .max_age = {self.float_lit(info['max_age_s'])}, .valid = false }};")
            elif info["kind"] == "latch":
                states.append(f"static {ct} s_{sname} = {{ .count = 0, .need = {info['need']}, .set = false }};")
            else:
                states.append(f"static {self.elem_ctype(ty)} s_{sname} = {self.scalar_lit(info['init'], ty)};")
        for pname, pinfo in ck.protected.items():
            for fname, finfo in pinfo["fields"].items():
                states.append(f"static {self.elem_ctype(finfo['ty'])} pf_{pname}_{fname} = "
                              f"{self.scalar_lit(finfo['init'], finfo['ty'])};")
        rq = [RQ_HELPERS[m] for m in ("floor", "ceil", "toward_zero", "nearest_even") if m in ck.rounding_used]
        if ck.protected:
            if self.freestanding:
                rq += ["extern uint32_t spelf_protected_enter(int32_t ceiling);",
                       "extern void spelf_protected_exit(int32_t ceiling, uint32_t saved);"]
            else:
                rq += ["static uint32_t spelf_protected_enter(int32_t ceiling) { (void)ceiling; return 0u; }",
                       "static void spelf_protected_exit(int32_t ceiling, uint32_t saved) { (void)ceiling; (void)saved; }"]
        for ename, enc in ck.encodings.items():
            d = enc["decl"]
            wt, st = arith_ctype(enc["wire"]), arith_ctype(enc["sem"])
            _, dc = self.expr(d.dexpr)
            _, ec = self.expr(d.eexpr)
            rq.append(f"static {st} spelf_decode_{ename}({wt} v_{d.dparam}) {{ return ({st})({dc}); }}")
            rq.append(f"static {wt} spelf_encode_{ename}({st} v_{d.eparam}) {{ return ({wt})({ec}); }}")
        uses_halt = any(isinstance(n, A.Halt) for n in _walk(ck.prog))
        protos, defs = [], []
        skip = set()
        if self.freestanding:
            for fn in ck.fns.values():
                if fn.is_extern:
                    continue
                if fn.name == "main" or any(isinstance(n, A.Call) and n.func == "print" for n in _walk(fn.body)):
                    skip.add(fn.name)
        called = {c.func for f in ck.fns.values() if f.body is not None and f.name not in skip
                  for c in _walk(f.body) if isinstance(c, A.Call) and c.func in ck.fns}
        exported = {n for n, f in ck.fns.items() if n not in called or any(a.name == "export" for a in f.annotations)}
        self.exported = exported
        for fn in ck.fns.values():
            if fn.name in skip:
                continue
            if fn.is_extern:
                protos.append(self.signature(fn) + ";")
                continue
            if self.freestanding and fn.name not in exported and getattr(fn, "_task", None) is None \
                    and getattr(fn, "_prot", None) is None:
                protos.append("static " + self.signature(fn) + ";")
            elif getattr(fn, "_task", None) is not None:
                protos.append(self.signature(fn) + ";")
            elif getattr(fn, "_prot", None) is not None:
                protos.append("static " + self.signature(fn) + ";")
            else:
                protos.append(("" if self.freestanding else "static ") + self.signature(fn) + ";")
        for fn in ck.fns.values():
            if fn.is_extern or fn.name in skip:
                continue
            sig = ck.sigs[fn.name]
            self.cur_ret = sig.ret
            self.cur_out = self.out_ret(fn)
            self.nrvo = self.nrvo_candidate(fn) if self.cur_out else None
            self.ptr_params = {n for n, t, _ in sig.params if self.by_ptr(t)}
            s = ("discard",) if isinstance(sig.ret, UnitT) else ("ret",)
            lines = self.block(fn.body, s)
            if getattr(fn, "_prot", None) is not None:
                ceil = ck.ceilings.get(fn._prot, 0)
                params = ", ".join(self.param_decl(n, t) for n, t, _ in sig.params) or "void"
                argn = ", ".join(f"v_{n}" for n, _, _ in sig.params)
                ret = self.ctype(sig.ret)
                body_name = f"{self.cname(fn)}__body"
                defs.append(f"static {ret} {body_name}({params}) {{")
                defs += self.indent(lines)
                defs.append("}")
                defs.append("static " + self.signature(fn) + " {")
                defs.append(f"    uint32_t saved = spelf_protected_enter({ceil});")
                if isinstance(sig.ret, UnitT):
                    defs += [f"    {body_name}({argn});", f"    spelf_protected_exit({ceil}, saved);"]
                else:
                    defs += [f"    {ret} r = {body_name}({argn});", f"    spelf_protected_exit({ceil}, saved);",
                             "    return r;"]
                defs.append("}")
                defs.append("")
                continue
            task = getattr(fn, "_task", None) is not None
            public = task or (self.freestanding and fn.name in exported)
            defs.append(("" if public else "static ") + self.signature(fn) + " {")
            defs += self.indent(lines)
            defs.append("}")
            defs.append("")
        main = []
        if "main" in ck.fns and not ck.fns["main"].is_extern and not self.freestanding:
            r = ck.sigs["main"].ret
            if isinstance(r, UnitT):
                main = ["int main(void) {", "    f_main();", "    return 0;", "}"]
            else:
                main = ["int main(void) {", "    return (int)f_main();", "}"]
        cache = []
        if getattr(self, "uses_cache_ops", False):
            if self.freestanding:
                cache = ["extern void spelf_cache_clean(const volatile void *p, uint32_t n);",
                         "extern void spelf_cache_invalidate(const volatile void *p, uint32_t n);",
                         "extern void spelf_mem_barrier(void);"]
            else:
                cache = ["static void spelf_cache_clean(const volatile void *p, uint32_t n) { (void)p; (void)n; }",
                         "static void spelf_cache_invalidate(const volatile void *p, uint32_t n) { (void)p; (void)n; }",
                         "static void spelf_mem_barrier(void) { }"]
        out = header + enums + [""] + self.typedefs + res_types + [""] + cache + [""]
        if uses_halt:
            out += ["static _Noreturn void spelf_halt(const char *why) { (void)why; for (;;) { } }", ""]
        table = []
        isrs = {n: t for n, t in ck.tasks.items() if t.get("kind") == "isr"}
        tasks_only = {n: t for n, t in ck.tasks.items() if t.get("kind") != "isr"}
        if ck.tasks:
            table = ["typedef struct { const char *name; uint32_t period_us; uint32_t deadline_us; "
                     "int32_t priority; uint32_t stack_bytes; void (*entry)(void); } spelf_task_t;",
                     "const spelf_task_t spelf_tasks[] = {"]
            for tname, t in tasks_only.items():
                table.append(f'    {{ "{tname}", {round(t["period"] * 1e6)}u, {round(t["deadline"] * 1e6)}u, '
                             f'{t["priority"]}, {t.get("stack", 0)}u, task_{tname} }},')
            if not tasks_only:
                table.append("    { 0, 0u, 0u, 0, 0u, 0 },")
            table += ["};", f"const uint32_t spelf_task_count = {len(tasks_only)}u;", ""]
            if isrs:
                table += ["typedef struct { const char *name; uint32_t vector; int32_t level; "
                          "uint32_t min_interarrival_us; void (*entry)(void); } spelf_isr_t;",
                          "const spelf_isr_t spelf_isrs[] = {"]
                for iname, t in isrs.items():
                    table.append(f'    {{ "{iname}", {t["vector"]}u, {t["level"]}, {round(t["period"] * 1e6)}u, '
                                 f'isr_{iname} }},')
                table += ["};", f"const uint32_t spelf_isr_count = {len(isrs)}u;", ""]
            if ck.schedules:
                table += self.schedule_tables(tasks_only)
        out += rq + [""] + self.helpers + [""] + consts + [""] + states + [""] + protos + [""] + defs + table + main
        return "\n".join(out) + "\n"

def _schedule_tables(self, tasks_only: dict) -> list:
    ck = self.ck
    modes = sorted(ck.modes)
    names = list(tasks_only)
    out = []
    for i, m in enumerate(modes):
        out.append(f"#define SPELF_MODE_{m} {i}u")
    out += [f"#define SPELF_MODE_COUNT {len(modes)}u",
            "typedef struct { const char *name; uint32_t mode_mask; const uint32_t *period_us; "
            "const uint32_t *deadline_us; } spelf_schedule_t;"]
    rows = []
    for k, sd in enumerate(ck.schedules):
        act = ck.schedule_sets[sd.name]
        mask = sum(1 << modes.index(m) for m in sd.modes)
        per = ", ".join(f"{round(act[t]['period'] * 1e6)}u" if t in act else "0u" for t in names) or "0u"
        dl = ", ".join(f"{round(act[t]['deadline'] * 1e6)}u" if t in act else "0u" for t in names) or "0u"
        out += [f"static const uint32_t spelf_sched{k}_period_us[] = {{ {per} }};",
                f"static const uint32_t spelf_sched{k}_deadline_us[] = {{ {dl} }};"]
        rows.append(f'    {{ "{sd.name}", 0x{mask:x}u, spelf_sched{k}_period_us, spelf_sched{k}_deadline_us }},')
    out += ["const spelf_schedule_t spelf_schedules[] = {"] + rows
    out += ["};", f"const uint32_t spelf_schedule_count = {len(ck.schedules)}u;",
            f"const uint32_t spelf_mode_count = {len(modes)}u;",
            "const uint8_t spelf_mode_schedule[] = { "
            + ", ".join(f"{[sd.name for sd in ck.schedules].index(ck.mode_schedule[m])}u" for m in modes) + " };", ""]
    return out

CGen.schedule_tables = _schedule_tables

def generate(ck: Checker, freestanding: bool = False, dma_coherent: bool = False, print_hook: bool = False) -> str:
    return CGen(ck, freestanding, dma_coherent, print_hook).generate()
