from . import ast as A
from . import units as U
from .diagnostics import SpelfError
from .lexer import Token, lex

SUSPECT_VARIANTS = {"Valid", "Saturated", "Stale", "Invalid"}
PUSH_VARIANTS = {"Ok", "Full"}
HELD_VARIANTS = {"Fresh", "Expired"}
CLAMP_VARIANTS = {"Unclamped", "Clamped"}
ROUNDING_MODES = {"nearest_even", "toward_zero", "floor", "ceil"}

class Parser:
    def __init__(self, toks: list[Token], filename: str):
        self.toks = toks
        self.pos = 0
        self.filename = filename

    @property
    def tok(self) -> Token:
        return self.toks[self.pos]

    def peek(self, k: int = 1) -> Token:
        return self.toks[min(self.pos + k, len(self.toks) - 1)]

    def err(self, msg: str, rule: str = "SYNTAX", tok: Token | None = None, note=None):
        t = tok or self.tok
        raise SpelfError(rule, msg, self.filename, t.line, t.col, note)

    def at(self, text: str) -> bool:
        return self.tok.kind in ("PUNCT", "KW") and self.tok.text == text

    def accept(self, text: str) -> bool:
        if self.at(text):
            self.pos += 1
            return True
        return False

    def expect(self, text: str) -> Token:
        if not self.at(text):
            self.err(f"expected '{text}', found '{self.tok.text or 'end of file'}'")
        t = self.tok
        self.pos += 1
        return t

    def ident(self) -> str:
        if self.tok.kind != "IDENT":
            self.err(f"expected an identifier, found '{self.tok.text or 'end of file'}'")
        t = self.tok.text
        self.pos += 1
        return t

    def int_lit(self, signed: bool = False) -> int:
        neg = False
        if signed and self.accept("-"):
            neg = True
        if self.tok.kind != "INT":
            self.err("expected an integer")
        v = int(self.tok.text)
        self.pos += 1
        return -v if neg else v

    def num_lit(self) -> float:
        neg = self.accept("-")
        if self.tok.kind not in ("INT", "FLOAT"):
            self.err("expected a number")
        v = float(self.tok.text)
        self.pos += 1
        return -v if neg else v

    def loc(self, node, tok: Token):
        node.line, node.col = tok.line, tok.col
        return node

    def program(self) -> A.Program:
        items = []
        while self.tok.kind != "EOF":
            items.append(self.item())
        return A.Program(items)

    def item(self):
        t = self.tok
        annots = []
        while self.at("@"):
            annots.append(self.annotation())
        if self.at("module"):
            self.pos += 1
            path = self.ident()
            while self.accept("."):
                path += "." + self.ident()
            mannots = []
            while self.at("@") and self.tok.line == t.line:
                mannots.append(self.annotation())
            self.accept(";")
            return self.loc(A.ModuleDecl(path, annots + mannots), t)
        if self.at("mode_lattice"):
            return self.mode_lattice()
        if self.at("flight_phase"):
            self.pos += 1
            self.expect("=")
            self.expect("{")
            modes = [self.ident()]
            while self.accept(","):
                if self.at("}"):
                    break
                modes.append(self.ident())
            self.expect("}")
            self.accept(";")
            return self.loc(A.FlightPhase(modes), t)
        if self.at("sealed"):
            self.pos += 1
            self.expect("enum")
            name = self.ident()
            self.expect("{")
            variants = [self.ident()]
            while self.accept(","):
                if self.at("}"):
                    break
                variants.append(self.ident())
            self.expect("}")
            return self.loc(A.EnumDecl(name, variants), t)
        if self.at("const"):
            self.pos += 1
            name = self.ident()
            self.expect(":")
            ty = self.type_()
            self.expect("=")
            val = self.expr()
            self.expect(";")
            return self.loc(A.ConstDecl(name, ty, val), t)
        if self.at("struct"):
            self.pos += 1
            name = self.ident()
            self.expect("{")
            fields = []
            while not self.at("}"):
                fname = self.ident()
                self.expect(":")
                fields.append((fname, self.type_()))
                if not self.accept(","):
                    break
            self.expect("}")
            inv = None
            if self.accept("invariant"):
                inv = self.expr()
            self.accept(";")
            return self.loc(A.StructDecl(name, fields, inv), t)
        if self.at("task") or self.at("isr"):
            kind = self.tok.text
            self.pos += 1
            name = self.ident()
            self.expect("(")
            props = {}
            while not self.at(")"):
                k = self.ident()
                self.expect("=")
                props[k] = self.expr()
                if not self.accept(","):
                    break
            self.expect(")")
            return self.loc(A.TaskDecl(name, props, self.block(), kind), t)
        if self.at("protected"):
            self.pos += 1
            name = self.ident()
            self.expect("{")
            fields, fns = [], []
            while not self.at("}"):
                if self.at("var"):
                    fields.append(self.let_or_var())
                    continue
                fannots = []
                while self.at("@"):
                    fannots.append(self.annotation())
                if not self.at("fn"):
                    self.err("a protected object may contain only 'var' fields and 'fn' operations")
                fns.append(self.fn_decl(fannots))
            self.expect("}")
            return self.loc(A.ProtectedDecl(name, fields, fns, annots), t)
        if self.at("encoded"):
            self.pos += 1
            name = self.ident()
            self.expect(":")
            wire = self.type_()
            self.expect("->")
            sem = self.type_()
            self.expect("{")
            got = {}
            while not self.at("}"):
                ft = self.tok
                k = self.ident()
                if k not in ("decode", "encode") or k in got:
                    self.err(f"encoded: unexpected '{k}' (decode and encode, once each)", tok=ft)
                self.expect("(")
                prm = self.ident()
                self.expect(")")
                self.expect("=")
                got[k] = (prm, self.expr())
                if not self.accept(","):
                    break
            self.expect("}")
            if set(got) != {"decode", "encode"}:
                self.err("an encoded declaration must define both decode and encode (R-202)", rule="R-202", tok=t)
            return self.loc(A.EncodedDecl(name, wire, sem, got["decode"][0], got["decode"][1],
                                          got["encode"][0], got["encode"][1]), t)
        if self.at("fault_hypothesis"):
            self.pos += 1
            name = self.ident()
            self.expect("{")
            failed, extra = [], {}
            while not self.at("}"):
                k = self.ident()
                self.expect("=")
                if k == "failed":
                    self.expect("{")
                    while not self.at("}"):
                        failed.append(self.ident())
                        if not self.accept(","):
                            break
                    self.expect("}")
                elif k in ("channel_faults", "healthy_start"):
                    if self.tok.kind != "INT":
                        self.err(f"fault_hypothesis: '{k}' must be an integer")
                    extra[k] = int(self.tok.text)
                    self.pos += 1
                elif k == "fault_mode":
                    extra[k] = self.ident()
                    if extra[k] not in ("omission", "value", "byzantine"):
                        self.err("fault_mode must be omission | value | byzantine", rule="R-412")
                else:
                    self.err(f"fault_hypothesis: unknown field '{k}' "
                             "(failed, channel_faults, fault_mode, healthy_start)")
                if not self.accept(","):
                    break
            self.expect("}")
            return self.loc(A.FaultHypothesis(name, failed, **extra), t)
        if self.at("redundancy"):
            self.pos += 1
            name = self.ident()
            self.expect("{")
            props = {}
            while not self.at("}"):
                k = self.ident()
                self.expect("=")
                if self.tok.kind == "INT":
                    props[k] = int(self.tok.text)
                    self.pos += 1
                elif self.accept("true"):
                    props[k] = True
                elif self.accept("false"):
                    props[k] = False
                else:
                    props[k] = self.ident()
                if not self.accept(","):
                    break
            self.expect("}")
            return self.loc(A.RedundancyDecl(name, props), t)
        if self.at("state"):
            self.pos += 1
            name = self.ident()
            self.expect(":")
            ty = self.type_()
            if not self.accept("="):
                self.err(f"state '{name}' cannot be declared without an initialiser", rule="00§0.2-E", tok=t)
            val = self.expr()
            self.expect(";")
            return self.loc(A.StateDecl(name, ty, val), t)
        if self.at("config"):
            self.pos += 1
            name = self.ident()
            self.expect("{")
            fields = []
            while not self.at("}"):
                ft = self.tok
                fname = self.ident()
                self.expect(":")
                fty = self.type_()
                self.expect("=")
                fval = self.expr()
                fields.append(self.loc(A.ConfigField(fname, fty, fval), ft))
                if not self.accept(","):
                    break
            self.expect("}")
            return self.loc(A.ConfigDecl(name, fields), t)
        if self.tok.kind == "IDENT" and self.tok.text == "schedule" and self.peek().kind == "IDENT":
            return self.schedule_decl()
        if self.tok.kind == "IDENT" and self.tok.text == "clock" and self.peek().text == "{":
            return self.clock_decl()
        if self.tok.kind == "IDENT" and self.tok.text == "type" and self.peek().kind == "IDENT"                 and self.peek(2).text == "=":
            self.pos += 1
            name = self.ident()
            self.expect("=")
            ty = self.type_()
            self.expect(";")
            return self.loc(A.TypeAlias(name, ty), t)
        if self.tok.kind == "IDENT" and self.tok.text == "resource" and self.peek().kind == "IDENT":
            return self.resource_decl()
        if self.tok.kind == "IDENT" and self.tok.text == "rotation" and self.peek().kind == "IDENT"                 and self.peek(2).text == ":":
            self.pos += 1
            name = self.ident()
            self.expect(":")
            src = self.ident()
            self.expect("->")
            dst = self.ident()
            self.expect("=")
            self.expect("[")
            rows = []
            while not self.at("]"):
                self.expect("[")
                row = []
                while not self.at("]"):
                    row.append(self.num_lit())
                    if not self.accept(","):
                        break
                self.expect("]")
                rows.append(row)
                if not self.accept(","):
                    break
            self.expect("]")
            self.expect(";")
            return self.loc(A.RotationDecl(name, src, dst, rows), t)
        if self.tok.kind == "IDENT" and self.tok.text == "transition" and self.peek().kind == "IDENT" \
                and self.peek(2).text == ":":
            return self.transition_decl()
        if self.tok.kind == "IDENT" and self.tok.text == "region" and self.peek().kind == "IDENT" \
                and self.peek(2).text == "{":
            self.pos += 1
            name = self.ident()
            self.expect("{")
            props = {}
            while not self.at("}"):
                k = self.ident()
                self.expect("=")
                props[k] = self.expr()
                if not self.accept(","):
                    break
            self.expect("}")
            self.accept(";")
            return self.loc(A.RegionDecl(name, props), t)
        if self.tok.kind == "IDENT" and self.tok.text == "watchdog" and self.peek().kind == "IDENT":
            self.pos += 1
            name = self.ident()
            self.expect("{")
            props = {}
            while not self.at("}"):
                k = self.ident()
                self.expect("=")
                props[k] = self.expr()
                if not self.accept(","):
                    break
            self.expect("}")
            self.accept(";")
            return self.loc(A.WatchdogDecl(name, props), t)
        if self.at("extern") or self.at("fn"):
            return self.fn_decl(annots)
        self.err(f"expected an item (fn, sealed enum, const, config, module...), found '{self.tok.text}'")

    def ident_set(self) -> list:
        self.expect("{")
        out = []
        while not self.at("}"):
            out.append(self.ident())
            if not self.accept(","):
                break
        self.expect("}")
        return out

    def resource_decl(self):
        t = self.tok
        self.pos += 1
        name = self.ident()
        states = self.ident_set()
        if not states:
            self.err(f"resource '{name}' must declare at least one state", rule="R-203", tok=t)
        region, device, read, write = None, [], None, None
        while True:
            if self.accept("in"):
                region = self.ident()
            elif self.tok.kind == "IDENT" and self.tok.text in ("device", "read", "write") and \
                    self.peek().text == "{":
                k = self.ident()
                vals = self.ident_set()
                if k == "device":
                    device = vals
                elif k == "read":
                    read = vals
                else:
                    write = vals
            else:
                break
        self.expect("{")
        fields = []
        while not self.at("}"):
            fname = self.ident()
            self.expect(":")
            fields.append((fname, self.type_()))
            if not self.accept(","):
                break
        self.expect("}")
        self.accept(";")
        return self.loc(A.ResourceDecl(name, states, region, device, read, write, fields), t)

    def transition_decl(self):
        t = self.tok
        self.pos += 1
        res = self.ident()
        self.expect(":")
        src = self.ident()
        self.expect("->")
        dst = self.ident()
        witness = None
        if self.tok.kind == "IDENT" and self.tok.text == "witness":
            self.pos += 1
            wn = self.ident()
            ws = None
            if self.accept("<"):
                ws = self.ident()
                self.expect(">")
            witness = (wn, ws)
        self.accept(";")
        return self.loc(A.TransitionDecl(res, src, dst, witness), t)

    def schedule_decl(self):
        t = self.tok
        self.pos += 1
        name = self.ident()
        self.expect("for")
        self.expect("{")
        modes = [self.ident()]
        while self.accept(","):
            if self.at("}"):
                break
            modes.append(self.ident())
        self.expect("}")
        self.expect("{")
        entries = []
        while not self.at("}"):
            et = self.tok
            task = self.ident()
            props = {}
            if self.accept("("):
                while not self.at(")"):
                    k = self.ident()
                    self.expect("=")
                    props[k] = self.expr()
                    if not self.accept(","):
                        break
                self.expect(")")
            entries.append((task, props, et.line, et.col))
            if not self.accept(","):
                break
        self.expect("}")
        self.accept(";")
        return self.loc(A.ScheduleDecl(name, modes, entries), t)

    def clock_decl(self):
        t = self.tok
        self.pos += 1
        self.expect("{")
        nominal, degraded = None, []
        while not self.at("}"):
            ft = self.tok
            k = self.ident()
            self.expect("=")
            if k == "nominal":
                nominal = self.expr()
            elif k == "degraded":
                if self.accept("{"):
                    while not self.at("}"):
                        degraded.append(self.expr())
                        if not self.accept(","):
                            break
                    self.expect("}")
                else:
                    degraded.append(self.expr())
            else:
                self.err(f"clock: unknown field '{k}' (nominal, degraded)", rule="R-307", tok=ft)
            if not self.accept(","):
                break
        self.expect("}")
        self.accept(";")
        if nominal is None:
            self.err("a clock declaration must include the 'nominal' frequency (WCETs are declared at that frequency)",
                     rule="R-307", tok=t)
        return self.loc(A.ClockDecl(nominal, degraded), t)

    def annotation(self) -> A.Annotation:
        t = self.expect("@")
        name = self.ident()
        args = []
        if self.accept("("):
            while not self.at(")"):
                if self.tok.kind == "IDENT" and self.peek().kind == "PUNCT" and self.peek().text == "=":
                    k = self.ident()
                    self.expect("=")
                    args.append((k, self.expr()))
                else:
                    args.append(self.expr())
                if not self.accept(","):
                    break
            self.expect(")")
        return self.loc(A.Annotation(name, args), t)

    def mode_lattice(self):
        t = self.expect("mode_lattice")
        name = self.ident()
        self.expect("{")
        edges = []
        while not self.at("}"):
            src = self.ident()
            self.expect("->")
            if self.accept("{"):
                dsts = [self.ident()]
                while self.accept(","):
                    dsts.append(self.ident())
                self.expect("}")
            else:
                dsts = [self.ident()]
            edges.append((src, dsts))
            if not self.accept(","):
                break
        self.expect("}")
        return self.loc(A.ModeLattice(name, edges), t)

    def fn_decl(self, annots) -> A.FnDecl:
        t = self.tok
        is_extern = self.accept("extern")
        self.expect("fn")
        name = self.ident()
        self.expect("(")
        params = []
        while not self.at(")"):
            pt = self.tok
            pname = self.ident()
            self.expect(":")
            params.append(self.loc(A.Param(pname, self.type_()), pt))
            if not self.accept(","):
                break
        self.expect(")")
        ret = self.type_() if self.accept("->") else None
        requires, ensures, effects, recursive = [], [], [], None
        while True:
            if self.accept("requires"):
                requires.append(self.expr())
            elif self.accept("ensures"):
                ensures.append(self.expr())
            elif self.accept("effects"):
                self.expect("{")
                while not self.at("}"):
                    effects.append(self.effect())
                    if not self.accept(","):
                        break
                self.expect("}")
            elif self.accept("recursive"):
                self.expect("(")
                self.ident()
                self.expect("<=")
                recursive = self.int_lit()
                self.expect(")")
            else:
                break
        body = None
        if is_extern:
            self.expect(";")
        else:
            body = self.block()
        return self.loc(A.FnDecl(name, params, ret, requires, ensures, effects, recursive,
                                 annots, body, is_extern), t)

    def effect(self) -> A.Effect:
        t = self.tok
        name = self.ident()
        if name == "no_alloc":
            return self.loc(A.Effect("no_alloc"), t)
        if name == "wcet":
            self.expect("<=")
            v = self.num_lit()
            unit = self.unit_brackets() if self.at("[") else U.base("s")
            return self.loc(A.Effect("wcet", (v, unit)), t)
        if name == "stack":
            self.expect("<=")
            if self.tok.kind != "INT":
                self.err("stack <= N: N must be an integer number of bytes")
            v = int(self.tok.text)
            self.pos += 1
            return self.loc(A.Effect("stack", v), t)
        if name == "io":
            self.expect("(")
            dev = self.ident()
            self.expect(")")
            return self.loc(A.Effect("io", dev), t)
        if name == "mode":
            self.expect("(")
            modes = [self.ident()]
            while self.accept("|"):
                modes.append(self.ident())
            self.expect(")")
            return self.loc(A.Effect("mode", modes), t)
        self.err(f"unknown effect '{name}' (no_alloc, wcet, mode, io)", tok=t)

    def unit_brackets(self):
        self.expect("[")
        u = self.unit_expr()
        self.expect("]")
        return u

    def unit_expr(self):
        u = self.unit_factor()
        while self.at("*") or self.at("/"):
            op = self.tok.text
            self.pos += 1
            r = self.unit_factor()
            u = u * r if op == "*" else u / r
        return u

    def unit_factor(self):
        t = self.tok
        if self.accept("("):
            u = self.unit_expr()
            self.expect(")")
        elif t.kind == "INT" and t.text == "1":
            self.pos += 1
            u = U.DIMENSIONLESS
        elif t.kind == "IDENT":
            self.pos += 1
            try:
                u = U.base(t.text)
            except KeyError:
                self.err(f"unknown unit '{t.text}'", rule="05§5.2", tok=t,
                         note="known units: " + ", ".join(U.known_units()))
        else:
            self.err("expected a unit", rule="05§5.2")
        if self.accept("^"):
            u = U.power(u, self.int_lit(signed=True))
        return u

    def type_(self):
        t = self.tok
        if self.accept("["):
            elem = self.type_()
            self.expect(";")
            n = self.int_lit()
            self.expect("]")
            return self.loc(A.TArray(elem, n), t)
        if self.accept("&"):
            return self.loc(A.TBorrow(self.type_()), t)
        if self.tok.kind != "IDENT":
            self.err("expected a type")
        name = self.ident()
        if name == "Int":
            self.expect("<")
            lo = self.int_lit(signed=True)
            self.expect("..=")
            hi = self.int_lit(signed=True)
            self.expect(">")
            if lo > hi:
                self.err(f"empty range type Int<{lo}..={hi}>", tok=t)
            return self.loc(A.TInt(lo, hi), t)
        if name in ("Float", "Float32"):
            self.expect("<")
            unit = self.unit_expr()
            frame = self.ident() if self.accept("@") else None
            lo = hi = None
            if self.accept(","):
                lo = self.num_lit()
                self.expect("..=")
                hi = self.num_lit()
            self.expect(">")
            return self.loc(A.TFloat(unit, frame, lo, hi, 32 if name == "Float32" else 64), t)
        if name == "bool":
            return self.loc(A.TBool(), t)
        if name == "Instant":
            self.expect("<")
            clock = self.ident()
            self.expect(">")
            return self.loc(A.TInstant(clock), t)
        if name == "Suspect":
            self.expect("<")
            inner = self.type_()
            self.expect(">")
            return self.loc(A.TSuspect(inner), t)
        if name == "Fix":
            self.expect("<")
            total = self.int_lit()
            self.expect(",")
            frac = self.int_lit()
            self.expect(",")
            unit = self.unit_expr()
            self.expect(">")
            if not (2 <= total <= 62 and 0 <= frac < total):
                self.err(f"Fix<{total}, {frac}, ...>: requires 2 ≤ total ≤ 62 and 0 ≤ fraction < total", tok=t)
            return self.loc(A.TFix(total, frac, unit), t)
        if name == "Wire":
            self.expect("<")
            n = self.ident()
            self.expect(">")
            return self.loc(A.TWire(n), t)
        if name == "Held":
            self.expect("<")
            inner = self.type_()
            self.expect(">")
            return self.loc(A.THeld(inner), t)
        if name == "Latch":
            return self.loc(A.TLatch(), t)
        if name == "Bounded":
            self.expect("<")
            elem = self.type_()
            self.expect(",")
            n = self.int_lit()
            self.expect(">")
            return self.loc(A.TBounded(elem, n), t)
        if name in ("i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "int", "f32", "f64"):
            self.err(f"'{name}' is not a Spelf type: integers are range types (Int<lo..=hi>), "
                     "floats carry a unit (Float<unit>)", rule="05§5.1", tok=t)
        if self.accept("<"):
            states = [self.ident()]
            while self.accept("|"):
                states.append(self.ident())
            self.expect(">")
            return self.loc(A.TRes(name, states), t)
        return self.loc(A.TName(name), t)

    def block(self) -> A.Block:
        t = self.expect("{")
        stmts, tail = [], None
        while not self.at("}"):
            st = self.tok
            if self.at("let") or self.at("var"):
                stmts.append(self.let_or_var())
                continue
            if self.at("for"):
                stmts.append(self.for_stmt())
                continue
            if self.at("return"):
                self.pos += 1
                val = None if self.at(";") else self.expr()
                self.expect(";")
                stmts.append(self.loc(A.Return(val), st))
                continue
            if self.at("halt"):
                stmts.append(self.halt_stmt())
                continue
            e = self.expr()
            if self.accept(";"):
                stmts.append(self.loc(A.ExprStmt(e), st))
            elif self.accept("="):
                if not isinstance(e, (A.Name, A.Index, A.Path)):
                    self.err("the left-hand side of an assignment must be a variable, an array element or a resource field", tok=st)
                v = self.expr()
                self.expect(";")
                stmts.append(self.loc(A.Assign(e, v), st))
            elif self.at("}"):
                tail = e
            elif isinstance(e, (A.IfExpr, A.Match)):
                stmts.append(self.loc(A.ExprStmt(e), st))
            else:
                self.err("expected ';'")
        self.expect("}")
        return self.loc(A.Block(stmts, tail), t)

    def let_or_var(self):
        t = self.tok
        is_var = self.tok.text == "var"
        self.pos += 1
        name = self.ident()
        ty = None
        if self.accept(":"):
            ty = self.type_()
        if is_var and ty is None:
            self.err("a mutable variable (var) must declare its type explicitly", rule="05§5.1", tok=t)
        if not self.accept("="):
            self.err(f"'{name}' cannot be declared without an initialiser: Spelf has no uninitialised state",
                     rule="00§0.2-E", tok=t,
                     note="Principle E: every value is initialised before it is used.")
        val = self.expr()
        self.expect(";")
        if is_var:
            return self.loc(A.Var(name, ty, val), t)
        return self.loc(A.Let(name, ty, val), t)

    def for_stmt(self):
        t = self.expect("for")
        var = self.ident()
        self.expect("in")
        lo = self.int_lit(signed=True)
        self.expect("..")
        if self.tok.kind != "INT":
            self.err("the loop upper bound must be a constant integer (JPL rule 2: every loop has a fixed upper bound)",
                     rule="00§0.4-2")
        hi = self.int_lit(signed=True)
        inv = self.expr() if self.accept("invariant") else None
        body = self.block()
        return self.loc(A.For(var, lo, hi, body, inv), t)

    def halt_stmt(self):
        t = self.expect("halt")
        because = allowed = None
        if self.accept("("):
            while not self.at(")"):
                k = self.ident()
                self.expect("=")
                if k == "because":
                    if self.tok.kind != "STRING":
                        self.err("because must be a string")
                    because = self.tok.text
                    self.pos += 1
                elif k == "allowed_in":
                    self.expect("{")
                    allowed = [self.ident()]
                    while self.accept(","):
                        allowed.append(self.ident())
                    self.expect("}")
                else:
                    self.err(f"unknown argument '{k}' for halt")
                if not self.accept(","):
                    break
            self.expect(")")
        self.expect(";")
        return self.loc(A.Halt(because, allowed), t)

    def expr(self):
        return self.or_expr()

    def or_expr(self):
        e = self.and_expr()
        while self.at("||"):
            t = self.tok
            self.pos += 1
            e = self.loc(A.Binary("||", e, self.and_expr()), t)
        return e

    def and_expr(self):
        e = self.cmp_expr()
        while self.at("&&"):
            t = self.tok
            self.pos += 1
            e = self.loc(A.Binary("&&", e, self.cmp_expr()), t)
        return e

    def cmp_expr(self):
        e = self.add_expr()
        if self.tok.kind == "PUNCT" and self.tok.text in ("==", "!=", "<", "<=", ">", ">="):
            t = self.tok
            self.pos += 1
            e = self.loc(A.Binary(t.text, e, self.add_expr()), t)
            if self.tok.kind == "PUNCT" and self.tok.text in ("==", "!=", "<", "<=", ">", ">="):
                self.err("comparisons cannot be chained; use &&")
        return e

    def add_expr(self):
        e = self.mul_expr()
        while self.at("+") or self.at("-"):
            t = self.tok
            self.pos += 1
            e = self.loc(A.Binary(t.text, e, self.mul_expr()), t)
        return e

    def mul_expr(self):
        e = self.unary()
        while self.at("*") or self.at("/") or self.at("%"):
            t = self.tok
            self.pos += 1
            e = self.loc(A.Binary(t.text, e, self.unary()), t)
        return e

    def unary(self):
        t = self.tok
        if self.accept("-"):
            return self.loc(A.Unary("-", self.unary()), t)
        if self.accept("!"):
            return self.loc(A.Unary("!", self.unary()), t)
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while self.at("["):
            t = self.tok
            self.pos += 1
            idx = self.expr()
            self.expect("]")
            e = self.loc(A.Index(e, idx), t)
        return e

    def primary(self):
        t = self.tok
        if t.kind in ("INT", "FLOAT"):
            self.pos += 1
            if self.at("["):
                unit = self.unit_brackets()
                return self.loc(A.FloatLit(float(t.text), unit), t)
            if t.kind == "INT":
                return self.loc(A.IntLit(int(t.text)), t)
            return self.loc(A.FloatLit(float(t.text), U.DIMENSIONLESS), t)
        if t.kind == "STRING":
            self.pos += 1
            return self.loc(A.StrLit(t.text), t)
        if self.accept("true"):
            return self.loc(A.BoolLit(True), t)
        if self.accept("false"):
            return self.loc(A.BoolLit(False), t)
        if self.accept("("):
            e = self.expr()
            self.expect(")")
            return e
        if self.accept("["):
            v = self.expr()
            self.expect(";")
            n = self.int_lit()
            self.expect("]")
            return self.loc(A.ArrayRepeat(v, n), t)
        if self.at("if"):
            return self.if_expr()
        if self.at("match"):
            return self.match_expr()
        if self.accept("narrow"):
            self.expect("<")
            target = self.type_()
            self.expect(">")
            self.expect("(")
            e = self.expr()
            self.expect(")")
            return self.loc(A.Narrow(target, e), t)
        if self.accept("convert"):
            self.expect("<")
            unit = self.unit_expr()
            self.expect(">")
            self.expect("(")
            e = self.expr()
            self.expect(")")
            return self.loc(A.Convert(unit, e), t)
        if t.kind == "IDENT" and t.text == "requantize" and self.peek().kind == "PUNCT" and self.peek().text == "<":
            self.pos += 1
            self.expect("<")
            target = self.type_()
            self.expect(">")
            self.expect("(")
            e = self.expr()
            rounding = None
            if self.accept(","):
                k = self.ident()
                if k != "rounding":
                    self.err(f"unknown argument '{k}' for requantize")
                self.expect("=")
                rt = self.tok
                rounding = self.ident()
                if rounding not in ROUNDING_MODES:
                    self.err(f"unknown rounding mode '{rounding}' "
                             f"(valid: {', '.join(sorted(ROUNDING_MODES))})", rule="05§5.1", tok=rt)
            self.expect(")")
            return self.loc(A.Requantize(target, e, rounding), t)
        if t.kind == "IDENT" and t.text in ("wire", "encode", "transit", "rotate") and self.peek().kind == "PUNCT" \
                and self.peek().text == "<":
            self.pos += 1
            self.expect("<")
            tname = self.ident()
            self.expect(">")
            self.expect("(")
            args = []
            while not self.at(")"):
                args.append(self.expr())
                if not self.accept(","):
                    break
            self.expect(")")
            return self.loc(A.GenericCall(t.text, tname, args), t)
        if t.kind == "IDENT" and self.peek().kind == "PUNCT" and self.peek().text == "{" \
                and self.peek(2).kind == "IDENT" and self.peek(3).kind == "PUNCT" and self.peek(3).text == ":":
            name = self.ident()
            self.expect("{")
            fields = []
            while not self.at("}"):
                fname = self.ident()
                self.expect(":")
                fields.append((fname, self.expr()))
                if not self.accept(","):
                    break
            self.expect("}")
            return self.loc(A.StructLit(name, fields), t)
        if t.kind == "IDENT":
            name = self.ident()
            if self.accept("::"):
                return self.loc(A.Path(name, self.ident(), "::"), t)
            if self.at(".") and self.peek().kind == "IDENT":
                self.pos += 1
                right = self.ident()
                if self.accept("("):
                    args = []
                    while not self.at(")"):
                        args.append(self.expr())
                        if not self.accept(","):
                            break
                    self.expect(")")
                    return self.loc(A.ProtCall(name, right, args), t)
                return self.loc(A.Path(name, right, "."), t)
            if self.accept("("):
                args, kwargs = [], {}
                while not self.at(")"):
                    if self.tok.kind == "IDENT" and self.peek().kind == "PUNCT" and self.peek().text == "=":
                        k = self.ident()
                        self.expect("=")
                        kwargs[k] = self.expr()
                    else:
                        if kwargs:
                            self.err("a positional argument cannot follow a named argument")
                        args.append(self.expr())
                    if not self.accept(","):
                        break
                self.expect(")")
                return self.loc(A.Call(name, args, kwargs), t)
            return self.loc(A.Name(name), t)
        self.err(f"expected an expression, found '{t.text or 'end of file'}'")

    def if_expr(self):
        t = self.expect("if")
        cond = self.expr()
        then = self.block()
        els = None
        if self.accept("else"):
            els = self.if_expr() if self.at("if") else self.block()
        return self.loc(A.IfExpr(cond, then, els), t)

    def match_expr(self):
        t = self.expect("match")
        scrut = self.expr()
        self.expect("{")
        arms = []
        while not self.at("}"):
            at = self.tok
            pat = self.pattern()
            self.expect("=>")
            if self.at("{"):
                body = self.block()
                arms.append(self.loc(A.Arm(pat, body), at))
                self.accept(",")
            else:
                body = self.expr()
                arms.append(self.loc(A.Arm(pat, body), at))
                if not self.accept(","):
                    break
        self.expect("}")
        return self.loc(A.Match(scrut, arms), t)

    def pattern(self):
        t = self.tok
        name = self.ident()
        if name == "_":
            return self.loc(A.Pattern("wild"), t)
        if name in SUSPECT_VARIANTS:
            bind = None
            if self.accept("("):
                bind = self.ident()
                self.expect(")")
            return self.loc(A.Pattern("suspect", variant=name, bind=bind), t)
        if name in PUSH_VARIANTS:
            return self.loc(A.Pattern("push", variant=name), t)
        if name in CLAMP_VARIANTS:
            self.expect("(")
            bind = self.ident()
            self.expect(")")
            return self.loc(A.Pattern("clamp", variant=name, bind=bind), t)
        if name in HELD_VARIANTS:
            bind = None
            if self.accept("("):
                bind = self.ident()
                self.expect(")")
            return self.loc(A.Pattern("held", variant=name, bind=bind), t)
        if self.accept("("):
            bind = self.ident()
            self.expect(")")
            return self.loc(A.Pattern("res", variant=name, bind=bind), t)
        self.expect("::")
        return self.loc(A.Pattern("enum", enum=name, variant=self.ident()), t)

def parse(src: str, filename: str = "<input>") -> A.Program:
    return Parser(lex(src, filename), filename).program()
