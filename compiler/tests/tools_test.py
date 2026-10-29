import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from run_tests import header

def run(cmd):
    return subprocess.run([sys.executable, *cmd], capture_output=True, text=True, cwd=ROOT,
                          encoding="utf-8", errors="replace")

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    fails, total_obl, n = [], 0, 0
    files = sorted(HERE.glob("mutants/*.spelf")) + sorted(HERE.glob("features/*.spelf")) + \
        sorted(HERE.glob("ok/*.spelf"))
    with tempfile.TemporaryDirectory() as d:
        for f in files:
            expect, _, _ = header(f)
            if expect != "OK":
                continue
            out = pathlib.Path(d) / f.stem
            cp = run(["-m", "spelfc", "evidence", str(f), "-o", str(out)])
            if cp.returncode != 0:
                fails.append(f"{f.name}: evidence bundle could not be produced\n{cp.stderr[-400:]}")
                continue
            rc = run(["tools/recheck_evidence.py", str(out)])
            n += 1
            line = rc.stdout.strip().splitlines()[0] if rc.stdout.strip() else ""
            try:
                total_obl += int(line.split(": ", 1)[1].split(" ", 1)[0])
            except (IndexError, ValueError):
                pass
            if rc.returncode != 0:
                fails.append(f"{f.name}: re-check FAILED\n{rc.stdout[-600:]}")
            else:
                print(f"OK     {f.name:44s} {line.split(': ', 1)[-1][:60]}")
    print(f"\nevidence bundle: {n} programs, {total_obl} obligations re-solved independently")

    src = (HERE / "features" / "stack-budget.spelf").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as d:
        tight = pathlib.Path(d) / "tight.spelf"
        tight.write_text(src.replace("stack = 512", "stack = 64"), encoding="utf-8")
        for t in ("leon3", "rv32", "r5"):
            ok = run(["-m", "spelfc", "stack", str(HERE / "features" / "stack-budget.spelf"), "--target", t])
            if ok.returncode != 0:
                fails.append(f"stack {t}: a sufficient budget was rejected\n{ok.stdout}{ok.stderr}")
        bad = run(["-m", "spelfc", "stack", str(tight), "--target", "leon3"])
        if bad.returncode == 0 or "R-201" not in bad.stderr:
            fails.append(f"stack leon3: 64 B budget (actual 192 B) NOT REJECTED\n{bad.stdout}")
        else:
            print("OK     stack budget: 512 B accepted (3 targets), 64 B rejected on LEON3 with R-201")
    sys.path.insert(0, str(ROOT))
    from spelfc import checker as _ck, codegen as _cg, parser as _ps
    src = (HERE / "ok" / "ccsds_primary_header.spelf").read_text(encoding="utf-8")
    flight = _cg.generate(_ck.check(_ps.parse(src, "c"), "c"), freestanding=True)
    if "\nint32_t f_decode_header(" not in flight and "\nint64_t f_decode_header(" not in flight:
        fails.append("D-19: in the flight build the root function f_decode_header is not exported")
    else:
        print("OK     D-19: root functions are exported in the flight build")
    for prog in ("T-typestate-dma.spelf", "T-typestate-union.spelf"):
        for t in ("leon3", "rv32", "r5"):
            cp = run(["-m", "spelfc", "mem", str(HERE / "features" / prog), "--target", t])
            if cp.returncode != 0 or ": OK" not in cp.stdout:
                fails.append(f"region {prog} {t}: could not be verified\n{cp.stdout}{cp.stderr}")
    print("OK     memory regions: checker layout = target object layout (2 programs × 3 targets)")
    ms = _cg.generate(_ck.check(_ps.parse((HERE / "features" / "T-mode-schedule.spelf").read_text(encoding="utf-8"),
                                          "m"), "m"), freestanding=True)
    ts = _cg.generate(_ck.check(_ps.parse((HERE / "features" / "T-typestate-union.spelf").read_text(encoding="utf-8"),
                                          "u"), "u"), freestanding=True)
    zig = [sys.executable, "-m", "ziglang", "cc"]
    with tempfile.TemporaryDirectory() as d:
        dd = pathlib.Path(d)
        (dd / "ms.c").write_text(ms, encoding="utf-8")
        (dd / "ts.c").write_text(ts, encoding="utf-8")
        for tgt in ("sparc-freestanding", "riscv32-freestanding", "arm-freestanding-eabi"):
            for src_, extra in ((ROOT / "runtime" / "spelf_baremetal.c", []),
                                (ROOT / "runtime" / "spelf_rtems.c", ["-I", str(HERE / "rtems_api_subset"),
                                                                      "-I", str(ROOT / "runtime")]),
                                (dd / "ms.c", ["-Wno-unused-variable"]), (dd / "ts.c", [])):
                cp = subprocess.run(zig + ["-target", tgt, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                                           "-ffreestanding", *extra, "-c", str(src_), "-o", str(dd / "o.o")],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                if cp.returncode != 0:
                    fails.append(f"substrate {tgt} {src_.name}: did not compile\n{cp.stderr[-600:]}")
    print("OK     substrates: bare metal + RTEMS 6 + generated code, 3 targets, -Werror")
    for tool, args in (("tools/mech_soundness.py", []), ("tools/mech_soundness.py", ["--self-test"]),
                       ("tools/mech_soundness_full.py", []), ("tools/mech_soundness_full.py", ["--self-test"]),
                       ("tools/prove_rules.py", [])):
        cp = run([tool, *args])
        if cp.returncode != 0:
            fails.append(f"{tool} {' '.join(args)}: failed\n{cp.stdout[-800:]}")
        else:
            print(f"OK     {tool} {' '.join(args)}: {cp.stdout.strip().splitlines()[-1][:90]}")
    if fails:
        print("\nFAILED:\n" + "\n".join(fails))
        sys.exit(1)
    print("toolchain tests passed")

if __name__ == "__main__":
    main()
