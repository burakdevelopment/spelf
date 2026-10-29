import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

from . import checker, codegen, parser
from .diagnostics import SpelfError

CFLAGS = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
          "-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-unused-but-set-variable"]

FLOAT_MODEL = "ieee"

def compile_source(path: pathlib.Path):
    src = path.read_text(encoding="utf-8")
    prog = parser.parse(src, str(path))
    return checker.check(prog, str(path), FLOAT_MODEL)

def report(ck) -> str:
    lines = ["PROOF REPORT"]
    total = sum(ck.stats.values())
    lines.append(f"  obligations: {total}, all proved (Z3, rlimit={checker.RLIMIT}, deterministic, "
                 f"floating-point model: {ck.float_model})")
    for rule in sorted(ck.stats):
        lines.append(f"    {rule:<8} {ck.stats[rule]}")
    wcet = [(fn.name, e.value) for fn in ck.fns.values() for e in fn.effects if e.kind == "wcet"]
    if wcet:
        lines.append(f"  WCET declared: {len(wcet)} functions, NOT VERIFIED (no hardware model; 03 §3.1)")
    ext = [fn.name for fn in ck.fns.values() if fn.is_extern]
    if ext:
        lines.append(f"  foreign boundary (extern): {', '.join(ext)}, contracts assumed at the boundary (R-701)")
    for n in ck.notes:
        lines.append(f"  note: {n}")
    return "\n".join(lines)

def build_c(path: pathlib.Path) -> tuple[str, object]:
    ck = compile_source(path)
    return codegen.generate(ck), ck

def run_c(csrc: str, stub: pathlib.Path | None, workdir: pathlib.Path) -> subprocess.CompletedProcess:
    cc = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
    if cc is None:
        raise RuntimeError("no C compiler found (gcc/cc/clang)")
    cfile = workdir / "out.c"
    cfile.write_text(csrc, encoding="utf-8")
    exe = workdir / ("out.exe" if sys.platform == "win32" else "out")
    cmd = [cc, *CFLAGS, str(cfile)] + ([str(stub)] if stub else []) + ["-o", str(exe)]
    cp = subprocess.run(cmd, capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError("C compilation failed:\n" + cp.stdout + cp.stderr)
    return subprocess.run([str(exe)], capture_output=True, text=True)

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(prog="spelfc", description="Spelf-Core compiler (prototype)")
    ap.add_argument("command", choices=["check", "build", "run", "stack", "mem", "interp", "evidence", "ir"])
    ap.add_argument("--float-model", default="ieee", choices=["ieee", "real"],
                    help="floating-point proof model: ieee (sound, default) | real (comparison only)")
    ap.add_argument("--target", default="leon3", choices=["leon3", "rv32", "r5"],
                    help="target for stack analysis (stack)")
    ap.add_argument("--rt-frame", type=int, default=None,
                    help="ASSUMED upper bound on the frame of compiler run-time calls (bytes)")
    ap.add_argument("file")
    ap.add_argument("-o", "--output")
    ap.add_argument("--stub", help="C implementation of the extern functions")
    ap.add_argument("--freestanding", action="store_true",
                    help="generate C for a flight target without an operating system (no stdio, no main)")
    ap.add_argument("--dma-coherent", action="store_true",
                    help="the target snoops DMA in the cache: no R-205 cache maintenance is generated (barriers only)")
    a = ap.parse_args(argv)
    global FLOAT_MODEL
    FLOAT_MODEL = a.float_model
    path = pathlib.Path(a.file)
    try:
        if a.command == "check":
            ck = compile_source(path)
            print(report(ck))
            return 0
        if a.command == "ir":
            from . import ir
            ck = compile_source(path)
            text = ir.dump(ck)
            if a.output:
                pathlib.Path(a.output).write_bytes(text.encode("utf-8"))
                print(f"Spelf-IR: {a.output}")
            else:
                sys.stdout.write(text + chr(10))
            return 0
        if a.command == "evidence":
            from . import evidence
            out = pathlib.Path(a.output) if a.output else path.with_suffix(".evidence")
            m = evidence.build(path, out, rt_frame=a.rt_frame)
            o = m["obligations"]
            print(f"EVIDENCE BUNDLE: {out}")
            print(f"  obligations {o['total']} ({', '.join(f'{k} {v}' for k, v in sorted(o['by_method'].items()))})")
            print(f"  assumptions: {len(m['assumptions']['externs'])} extern, "
                  f"{len(m['assumptions']['fault_hypotheses'])} fault hypotheses, "
                  f"{len(m['assumptions']['wcet'])} unverified WCET")
            for t, r in m["stack"].items():
                if "error" in r:
                    print(f"  stack {t}: analysis failed")
                else:
                    worst = max((e["depth"] or 0 for e in r["entries"].values()), default=0)
                    print(f"  stack {t}: worst {worst} B")
            print(f"  re-check: python tools/recheck_evidence.py {out}")
            return 0
        if a.command == "stack":
            from . import stack
            ck = compile_source(path)
            res = stack.analyze(ck, codegen.generate(ck, freestanding=True), a.target, a.rt_frame)
            print(stack.render(res))
            bad = [t for t, r in res["tasks"].items() if not r["ok"]]
            if bad:
                print(f"error [R-201] stack budget exceeded or not provable: {', '.join(bad)}", file=sys.stderr)
                return 1
            return 0
        if a.command == "mem":
            from . import memory
            ck = compile_source(path)
            res = memory.analyze(ck, codegen.generate(ck, freestanding=True), a.target)
            print(memory.render(res))
            bad = [r for r, v in res["regions"].items() if not v["ok"]]
            if bad:
                print(f"error [R-209] region could not be verified: {', '.join(bad)}", file=sys.stderr)
                return 1
            return 0
        if a.command == "interp":
            from . import interp
            ck = compile_source(path)
            try:
                out, r = interp.run(ck)
            except interp.Violation as v:
                print(f"ORACLE VIOLATION {v}", file=sys.stderr)
                return 2
            for line in out:
                print(line)
            return int(r) if isinstance(r, int) and not isinstance(r, bool) else 0
        if a.command == "build" and a.freestanding:
            ck = compile_source(path)
            csrc = codegen.generate(ck, freestanding=True, dma_coherent=a.dma_coherent)
        else:
            csrc, ck = build_c(path)
        if a.command == "build":
            out = pathlib.Path(a.output) if a.output else path.with_suffix(".c")
            out.write_text(csrc, encoding="utf-8")
            print(report(ck))
            print(f"  C output: {out}")
            return 0
        with tempfile.TemporaryDirectory() as d:
            cp = run_c(csrc, pathlib.Path(a.stub) if a.stub else None, pathlib.Path(d))
            sys.stdout.write(cp.stdout)
            sys.stderr.write(cp.stderr)
            return cp.returncode
    except SpelfError as e:
        print(e.render(), file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
