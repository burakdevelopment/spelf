import hashlib
import json
import pathlib
import sys

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    d = pathlib.Path(sys.argv[1])
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    bad = []
    src = d / "source" / m["source"]["file"]
    if hashlib.sha256(src.read_bytes()).hexdigest() != m["source"]["sha256"]:
        bad.append("source hash does not match")
    for rel, h in m["outputs"].items():
        if hashlib.sha256((d / rel).read_bytes()).hexdigest() != h:
            bad.append(f"{rel} hash does not match")

    import z3
    try:
        import cvc5
        have_cvc5 = True
    except ImportError:
        have_cvc5 = False

    n_smt = 0
    for e in m["obligations"]["index"]:
        rel = e.get("smt2")
        if rel is None:
            continue
        n_smt += 1
        text = (d / rel).read_text(encoding="utf-8")
        s = z3.Solver()
        s.set("rlimit", 4 * 20_000_000)
        s.add(z3.parse_smt2_string(text))
        r = s.check()
        if r != z3.unsat:
            bad.append(f"#{e['id']} ({e['rule']}, line {e['line']}): Z3 → {r}")
        if have_cvc5:
            import cvc5
            from cvc5 import InputParser, SymbolManager
            tm = cvc5.TermManager()
            slv = cvc5.Solver(tm)
            slv.setOption("produce-models", "false")
            sm = SymbolManager(tm)
            ip = InputParser(slv, sm)
            ip.setStringInput(cvc5.InputLanguage.SMT_LIB_2_6, "(set-logic ALL)\n" + text, "q")
            res = None
            while True:
                cmd = ip.nextCommand()
                if cmd.isNull():
                    break
                out = cmd.invoke(slv, sm)
                if out.strip() in ("sat", "unsat", "unknown"):
                    res = out.strip()
            if res != "unsat":
                bad.append(f"#{e['id']} ({e['rule']}, line {e['line']}): cvc5 → {res}")
    solvers = "Z3 + cvc5" if have_cvc5 else "Z3 (cvc5 not installed, NO solver diversity)"
    print(f"{m['source']['file']}: {n_smt} SMT obligations re-solved [{solvers}]; "
          f"{m['obligations']['total'] - n_smt} obligations discharged by range analysis or the simplifier (no SMT file)")
    if bad:
        print("FAILED:\n  " + "\n  ".join(bad))
        sys.exit(1)
    print("OK: hashes consistent, every query unsat")

if __name__ == "__main__":
    main()
