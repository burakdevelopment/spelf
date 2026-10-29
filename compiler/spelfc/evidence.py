import datetime
import hashlib
import json
import pathlib
import platform
import subprocess
import sys

import z3

from . import ast as A
from . import checker as C
from . import codegen, parser

VERSION = "0.3.0"

_TR = str.maketrans({chr(c): a for c, a in ((0xE7, "c"), (0xC7, "C"), (0x11F, "g"), (0x11E, "G"), (0x131, "i"),
                                             (0x130, "I"), (0xF6, "o"), (0xD6, "O"), (0x15F, "s"), (0x15E, "S"),
                                             (0xFC, "u"), (0xDC, "U"), (0x2212, "-"), (0x2192, "->"))})

def _ascii_smt(text: str) -> str:
    out = []
    for ch in text.translate(_TR):
        out.append(ch if ord(ch) < 128 else f"_u{ord(ch):04x}")
    return "".join(out)

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def tool_identity() -> dict:
    here = pathlib.Path(__file__).resolve().parent
    files = sorted(here.glob("*.py"))
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    sub = here.parent / "runtime" / "spelf_baremetal.c"
    try:
        zig = subprocess.run([sys.executable, "-m", "ziglang", "version"], capture_output=True, text=True).stdout.strip()
    except OSError:
        zig = None
    return {"spelfc": VERSION, "spelfc_sources_sha256": h.hexdigest(), "python": platform.python_version(),
            "z3": z3.get_version_string(), "zig_cc": zig, "rlimit": C.RLIMIT,
            "substrate_sha256": _sha(sub.read_bytes()) if sub.exists() else None}

def _assumptions(ck) -> dict:
    ext = []
    for fn in ck.fns.values():
        if fn.is_extern:
            sig = ck.sigs[fn.name]
            ext.append({"name": fn.name, "params": [f"{n}: {t}" for n, t, _ in sig.params], "ret": str(sig.ret),
                        "effects": [f"{e.kind}({e.value})" if e.value is not None else e.kind for e in fn.effects],
                        "assumed": "return value within the range of the declared type (R-701 boundary contract)"})
    hyps = [{"name": h.name, "failed_devices": h.failed, "channel_faults": h.channel_faults,
             "fault_mode": h.fault_mode, "healthy_start": h.healthy_start} for h in ck.hypotheses.values()]
    bounds = []
    for fn in ck.fns.values():
        for a in fn.annotations:
            if a.name == "output_bound":
                kw = {k: v for k, v in (x for x in a.args if isinstance(x, tuple))}
                bounds.append({"fn": fn.name, **{k: _expr_text(v) for k, v in kw.items()},
                               "claim": "within the horizon only; no claim beyond it (R-605)"})
    wcet = [{"fn": fn.name, "declared": _expr_text(e.value), "status": "NOT VERIFIED (no hardware timing model)"}
            for fn in ck.fns.values() for e in fn.effects if e.kind == "wcet"]
    return {"externs": ext, "fault_hypotheses": hyps, "output_bounds": bounds, "wcet": wcet,
            "float_model": ("IEEE-754 binary32/binary64, round to nearest even; bit-exact with the generated C" if ck.fp else "real numbers (--float-model real): IEEE-754 rounding not modelled, for comparison only"),
            "solver": f"Z3 {z3.get_version_string()}, deterministic rlimit={C.RLIMIT}; SMT files can be re-checked"}

def _expr_text(e) -> str:
    if isinstance(e, A.IntLit):
        return str(e.value)
    if isinstance(e, A.FloatLit):
        u = str(e.unit)
        return f"{e.value}" + (f"[{u}]" if u not in ("1", "") else "")
    if isinstance(e, A.Name):
        return e.name
    return type(e).__name__

def _wcet_interface(ck) -> dict:
    loops, rec = [], []
    for name, fn in ck.fns.items():
        if fn.body is None:
            continue
        for n in codegen._walk(fn.body):
            if isinstance(n, A.For):
                loops.append({"fn": name, "line": n.line, "iterations": max(0, n.hi - n.lo)})
            elif isinstance(n, A.ArrayRepeat):
                loops.append({"fn": name, "line": n.line, "iterations": n.n, "kind": "array initialisation"})
        if fn.recursive is not None:
            rec.append({"fn": name, "max_depth": fn.recursive + 1})
    sched = {t: {"wcet_s": i.get("wcet"), "response_s": i.get("response"), "deadline_s": i.get("deadline")}
             for t, i in ck.tasks.items()}
    return {"loops": loops, "recursion": rec, "function_pointers": 0,
            "schedulability": sched}

def _architecture(ck) -> dict:
    tasks = {n: {"period_s": t.get("period"), "deadline_s": t.get("deadline"), "priority": t.get("priority"),
                 "stack_budget_B": t.get("stack")} for n, t in ck.tasks.items()}
    return {"tasks": tasks, "protected_ceilings": dict(ck.ceilings),
            "redundancy": {n: r.props for n, r in ck.redundancy.items()},
            "states": {n: str(i["ty"]) for n, i in ck.states.items()},
            "modes": sorted(ck.modes), "flight_phase": sorted(ck.flight)}

def build(path: pathlib.Path, outdir: pathlib.Path, targets=("leon3", "rv32", "r5"),
          rt_frame: int | None = None) -> dict:
    src = path.read_text(encoding="utf-8")
    prog = parser.parse(src, str(path))
    ck = C.Checker(prog, str(path))
    ck.evidence = []
    ck.check()
    hosted = codegen.generate(ck)
    flight = codegen.generate(ck, freestanding=True)

    outdir.mkdir(parents=True, exist_ok=True)
    for sub in ("source", "c", "obligations"):
        (outdir / sub).mkdir(exist_ok=True)
    for old in (outdir / "obligations").glob("*.smt2"):
        old.unlink()
    (outdir / "source" / path.name).write_bytes((src).encode("utf-8"))
    (outdir / "c" / "hosted.c").write_bytes((hosted).encode("utf-8"))
    (outdir / "c" / "flight.c").write_bytes((flight).encode("utf-8"))
    from . import ir
    (outdir / "ir.json").write_bytes(ir.dump(ck).encode("utf-8"))

    index = []
    for i, ev in enumerate(ck.evidence, 1):
        entry = {"id": i, "rule": ev["rule"], "line": ev["line"], "col": ev["col"], "fn": ev["fn"],
                 "what": ev["what"], "how": ev["how"]}
        if ev["smt2"] is not None:
            name = f"{i:04d}.smt2"
            hdr = (f"; spelfc obligation #{i}, rule {ev['rule']}, {path.name}:{ev['line']}:{ev['col']}\n"
                   f"; {ev['what']}\n; expected: unsat (the negated goal cannot be satisfied)\n")
            (outdir / "obligations" / name).write_bytes((hdr + _ascii_smt(ev["smt2"])).encode("utf-8"))
            entry["smt2"] = f"obligations/{name}"
        index.append(entry)

    stacks = {}
    for t in targets:
        try:
            from . import stack
            stacks[t] = stack.analyze(ck, flight, t, rt_frame)
        except Exception as ex:
            stacks[t] = {"error": str(ex)[:300]}

    by_how = {}
    for e in index:
        by_how[e["how"]] = by_how.get(e["how"], 0) + 1
    manifest = {
        "format": "spelf-evidence/1",
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "tool": tool_identity(),
        "source": {"file": path.name, "sha256": _sha(src.encode("utf-8")),
                   "module": ck.module.path if ck.module else None},
        "outputs": {"c/hosted.c": _sha(hosted.encode()), "c/flight.c": _sha(flight.encode()),
                    "ir.json": _sha((outdir / "ir.json").read_bytes())},
        "obligations": {"total": len(index), "by_rule": dict(sorted(ck.stats.items())), "by_method": by_how,
                        "index": index},
        "assumptions": _assumptions(ck),
        "architecture": _architecture(ck),
        "wcet_interface": _wcet_interface(ck),
        "stack": {t: ({k: v for k, v in r.items() if k != "functions"} if "error" not in r else r)
                  for t, r in stacks.items()},
        "notes": list(ck.notes),
        "limitations": [
            ("Floating point is modelled as IEEE-754 (binary32/binary64, round to nearest even); the semantics are "
             "bit-identical to the generated C." if ck.fp else
             "Floating point is in the REAL-NUMBER model in this build (--float-model real); IEEE-754 rounding "
             "is not modelled. For comparison only; not accepted at L2 and above."),
            "WCET declarations are not verified.",
            "Every obligation (including those discharged by range analysis or the simplifier) is written as an "
            "independently checkable SMT query; a few obligations without symbols are the exception.",
            "The tool is not qualified (DO-330); this bundle shows the form that qualification evidence takes.",
        ],
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "ASSURANCE.md").write_text(render_summary(manifest), encoding="utf-8")
    return manifest

def render_summary(m: dict) -> str:
    o = m["obligations"]
    L = [f"# Assurance summary: {m['source']['file']}", "",
         f"Created: {m['created_utc']} · spelfc {m['tool']['spelfc']} · Z3 {m['tool']['z3']} · "
         f"source sha256 `{m['source']['sha256'][:16]}…`", "",
         "## Proof obligations", "",
         f"Total **{o['total']}**, all proved. Methods: " +
         ", ".join(f"{k} {v}" for k, v in sorted(o["by_method"].items())) + ".", "",
         "| Rule | Count |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in o["by_rule"].items()]
    a = m["assumptions"]
    L += ["", "## Assumptions (what the claim rests on and is not proved)", ""]
    for e in a["externs"]:
        L.append(f"- extern `{e['name']}({', '.join(e['params'])}) -> {e['ret']}` — {e['assumed']}")
    for h in a["fault_hypotheses"]:
        L.append(f"- fault hypothesis `{h['name']}`: devices {h['failed_devices']}, f = {h['channel_faults']}, "
                 f"mode = {h['fault_mode']}, healthy cycles at start-up = {h['healthy_start']}")
    for b in a["output_bounds"]:
        L.append(f"- output bound `{b['fn']}`: horizon {b.get('horizon')}, tolerance {b.get('tolerance')}, "
                 f"step {b.get('max_step')}, bound {b.get('bound')}: {b['claim']}")
    for w in a["wcet"]:
        L.append(f"- WCET `{w['fn']}` ≤ {w['declared']} — {w['status']}")
    L.append(f"- floating-point model: {a['float_model']}")
    L += ["", "## Stack (R-201)", ""]
    for t, r in m["stack"].items():
        if "error" in r:
            L.append(f"- {t}: analysis failed: {r['error']}")
            continue
        worst = max((e["depth"] for e in r["entries"].values() if e["depth"] is not None), default=0)
        unk = [n for n, e in r["entries"].items() if e["depth"] is None]
        L.append(f"- {t}: worst entry depth {worst} B" + (f"; unknown: {unk}" if unk else "")
                 + "".join(f"; task {n} {g['depth']} B / budget {g['budget']}" for n, g in r["tasks"].items()))
    L += ["", "## Notes", ""] + [f"- {n}" for n in m["notes"]]
    L += ["", "## Limits", ""] + [f"- {x}" for x in m["limitations"]]
    L += ["", "Re-check: `python tools/recheck_evidence.py <this directory>`", ""]
    return "\n".join(L)
