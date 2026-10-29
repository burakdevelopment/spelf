from . import ast as A
from .diagnostics import SpelfError
from .stypes import BorrowT, ResT

class _St:
    __slots__ = ("vars", "taken")

    def __init__(self, vars_=None, taken=None):
        self.vars = dict(vars_ or {})
        self.taken = set(taken or ())

    def copy(self):
        return _St(self.vars, self.taken)

class Linear:
    def __init__(self, ck):
        self.ck = ck
        self._takes = {}
        self.fn = None

    def err(self, msg, node, note=None):
        raise SpelfError("R-203", msg, self.ck.filename, getattr(node, "line", 0), getattr(node, "col", 0), note)

    def takes(self, fname: str) -> frozenset:
        if fname not in self._takes:
            out = set()
            for n in self.ck.call_closure(fname):
                body = self.ck.fns[n].body
                if body is None:
                    continue
                for c in _walk(body):
                    if isinstance(c, A.Call) and c.func in ("take", "put") and c.args and isinstance(c.args[0], A.Name):
                        out.add(c.args[0].name)
            self._takes[fname] = frozenset(out)
        return self._takes[fname]

    @staticmethod
    def is_res(ty) -> bool:
        return isinstance(ty, ResT)

    def check_fn(self, fn):
        self.fn = fn
        sig = self.ck.sigs[fn.name]
        st = _St()
        for n, t, _ in sig.params:
            if isinstance(t, ResT):
                st.vars[n] = "live"
        ret_ctx = "move" if isinstance(sig.ret, ResT) else "use"
        diverged = self.block(fn.body, st, ret_ctx)
        if not diverged:
            self.exit_check(st, fn.body, "at the end of the function")

    def exit_check(self, st: _St, node, where: str):
        live = sorted(n for n, s in st.vars.items() if s == "live")
        if live:
            self.err(f"resource leak {where}: {', '.join(live)} not consumed", node,
                     "Every resource is consumed exactly once on every path: returned, passed to a consuming parameter, "
                     "put back into its state variable, or moved to a new state with transit. A dropped DMA "
                     "buffer can never be taken back (02 §2.7a).")
        if st.taken:
            self.err(f"leaving {where} without putting back a taken state: {', '.join(sorted(st.taken))}", node,
                     "A resource taken with take(x) is put back with put(x, …) in the same function; otherwise the next "
                     "activation would find the state empty.")

    def block(self, b: A.Block, st: _St, tail_ctx: str) -> bool:
        declared = []
        for s in b.stmts:
            if self.stmt(s, st, declared):
                return True
        if b.tail is not None:
            if self.expr(b.tail, st, tail_ctx):
                return True
        for n in declared:
            if st.vars.get(n) == "live":
                self.err(f"resource leak: '{n}' not consumed at the end of its scope", b,
                         "A resource cannot be dropped (linear type). Return it, put it back, or pass it to a consuming "
                         "function.")
            st.vars.pop(n, None)
        return False

    def stmt(self, s, st: _St, declared: list) -> bool:
        if isinstance(s, A.Let):
            if self.expr(s.value, st, "move"):
                return True
            if self.is_res(getattr(s, "_ty", None)):
                st.vars[s.name] = "live"
                declared.append(s.name)
            return False
        if isinstance(s, A.Var):
            if self.is_res(getattr(s, "_ty", None)):
                self.err(f"resource '{s.name}' cannot be declared with var: resources are moved, not reassigned", s)
            return self.expr(s.value, st, "use")
        if isinstance(s, A.Assign):
            t = s.target
            path = t if isinstance(t, A.Path) else (t.base if isinstance(t, A.Index) else None)
            if isinstance(path, A.Path) and getattr(path, "_ref", ("",))[0] == "rfield":
                self.need_live(path.left, st, path)
            if isinstance(t, A.Index):
                if self.expr(t.index, st, "use"):
                    return True
            return self.expr(s.value, st, "use")
        if isinstance(s, A.Return):
            if s.value is not None and self.expr(s.value, st, "move"):
                return True
            self.exit_check(st, s, "on return")
            return True
        if isinstance(s, A.Halt):
            return True
        if isinstance(s, A.For):
            before = st.copy()
            inner = st.copy()
            if self.block(s.body, inner, "use"):
                return False
            for n, stat in before.vars.items():
                if inner.vars.get(n) != stat:
                    self.err(f"the loop body changes the state of the outer resource '{n}'", s,
                             "A resource cannot be consumed once per iteration; take and consume it inside the loop.")
            if inner.taken != before.taken:
                self.err("the loop body unbalances take/put: each iteration must put back what it takes", s)
            return False
        if isinstance(s, A.ExprStmt):
            return self.expr(s.expr, st, "discard")
        return False

    def need_live(self, name: str, st: _St, node):
        stat = st.vars.get(name)
        if stat == "moved":
            self.err(f"resource '{name}' used after it was consumed", node,
                     "A moved (consumed) resource can no longer be reached by that name: it may have changed state or "
                     "passed to another owner. This makes errors such as 'handing over a half-assembled buffer' "
                     "impossible at compile time (F-09).")

    def expr(self, e, st: _St, ctx: str) -> bool:
        if e is None:
            return False
        if isinstance(e, A.Name):
            if e.name in st.vars:
                self.need_live(e.name, st, e)
                if ctx == "move":
                    st.vars[e.name] = "moved"
                elif ctx != "borrow":
                    self.err(f"resource '{e.name}' cannot be used in this context", e)
            return False
        if isinstance(e, A.Path):
            if getattr(e, "_ref", ("",))[0] == "rfield":
                self.need_live(e.left, st, e)
            return False
        if isinstance(e, A.Call):
            return self.call(e, st)
        if isinstance(e, A.GenericCall):
            if e.func == "transit":
                for a in e.args:
                    if self.expr(a, st, "move"):
                        return True
                return False
            return any(self.expr(a, st, "use") for a in e.args)
        if isinstance(e, (A.IfExpr, A.Match)):
            return self.branch(e, st, ctx)
        if isinstance(e, A.Block):
            return self.block(e, st, ctx)
        if isinstance(e, A.Index):
            if self.expr(e.base, st, "borrow"):
                return True
            return self.expr(e.index, st, "use")
        for c in _children(e):
            if self.expr(c, st, "use"):
                return True
        return False

    def call(self, e: A.Call, st: _St) -> bool:
        kind = getattr(e, "_callee", None)
        if kind == "take":
            nm = e.args[0].name
            if nm in st.taken:
                self.err(f"'{nm}' has already been taken: the same resource cannot be taken twice", e)
            st.taken.add(nm)
            return False
        if kind == "put":
            nm = e.args[0].name
            if self.expr(e.args[1], st, "move"):
                return True
            if nm not in st.taken:
                self.err(f"put({nm}, …): '{nm}' must first be taken with take (the old value would be dropped)", e)
            st.taken.discard(nm)
            return False
        if kind in ("user", "extern"):
            ptys = [t for _, t, _ in self.ck.sigs[e.func].params]
            for a, pt in zip(e.args, ptys):
                ctx = "move" if isinstance(pt, ResT) else ("borrow" if isinstance(pt, BorrowT) else "use")
                if self.expr(a, st, ctx):
                    return True
            clash = st.taken & self.takes(e.func) if kind == "user" else set()
            if clash:
                self.err(f"the call to '{e.func}' reaches {', '.join(sorted(clash))}, which is currently taken",
                         e, "The called function would find the state empty. Put it back first, or pass the resource as a "
                            "parameter.")
            return False
        for a in e.args:
            if self.expr(a, st, "use"):
                return True
        for a in e.kwargs.values():
            if self.expr(a, st, "use"):
                return True
        return False

    def branch(self, e, st: _St, ctx: str) -> bool:
        arms = []
        if isinstance(e, A.IfExpr):
            if self.expr(e.cond, st, "use"):
                return True
            arms = [(e.then, None), (e.els, None)]
        else:
            if self.expr(e.scrutinee, st, "use" if getattr(e, "_kind", "") != "res" else "move"):
                return True
            arms = [(a.body, a.pattern.bind if getattr(a, "_bind_ty", None) is not None else None) for a in e.arms]
        outs = []
        for body, bind in arms:
            s2 = st.copy()
            if body is None:
                outs.append(s2)
                continue
            if bind is not None:
                s2.vars[bind] = "live"
            if isinstance(body, A.Block):
                div = self.block(body, s2, ctx)
            else:
                div = self.expr(body, s2, ctx)
            if div:
                continue
            if bind is not None:
                if s2.vars.get(bind) == "live":
                    self.err(f"resource leak: '{bind}' not consumed in this arm", body)
                s2.vars.pop(bind, None)
            outs.append(s2)
        if not outs:
            return True
        first = outs[0]
        for o in outs[1:]:
            for n in set(first.vars) | set(o.vars):
                if first.vars.get(n) != o.vars.get(n):
                    self.err(f"resource '{n}' is consumed only in some branches", e,
                             "Whether a resource is still live after a branch cannot be left undecided: every branch must "
                             "consume the same resources.")
            if first.taken != o.taken:
                self.err("branches disagree on take/put balance: "
                         f"{', '.join(sorted(first.taken ^ o.taken))}", e)
        st.vars, st.taken = dict(first.vars), set(first.taken)
        return False

def _children(e):
    for f in getattr(e, "__dataclass_fields__", {}):
        if f in ("line", "col"):
            continue
        v = getattr(e, f)
        if isinstance(v, A.Node):
            yield v
        elif isinstance(v, (list, tuple)):
            for x in v:
                if isinstance(x, A.Node):
                    yield x
                elif isinstance(x, tuple):
                    for y in x:
                        if isinstance(y, A.Node):
                            yield y

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

def check_linear(ck):
    lin = Linear(ck)
    for name, fn in ck.fns.items():
        if fn.is_extern or fn.body is None:
            continue
        lin.check_fn(fn)
    ck.notes.append(f"linearity (R-203): {len(ck.resources)} resource types, every path checked")
