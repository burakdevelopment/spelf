import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from programs_a import P as PA
from programs_b import P as PB
from programs_c import P as PC

PROGRAMS = {**PA, **PB, **PC}
HOME = pathlib.Path(os.path.expanduser("~"))
CARGO = HOME / ".cargo" / "bin"
def _cppcheck():
    return shutil.which("cppcheck") or next((str(p) for p in [pathlib.Path("C:/Program Files/Cppcheck/cppcheck.exe")]
                                             if p.exists()), None)
TIMEOUT = 300

def sh(cmd, cwd=None, timeout=TIMEOUT, env=None):
    if env is not None:
        cmd = [shutil.which(cmd[0], path=env["PATH"]) or cmd[0]] + list(cmd[1:])
    try:
        cp = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                            encoding="utf-8", errors="replace")
        return cp.returncode, cp.stdout + cp.stderr
    except subprocess.TimeoutExpired:
        return -999, "TIMEOUT"
    except FileNotFoundError as e:
        return -998, f"TOOL MISSING: {e}"

def norm(msgs):
    out = set()
    for m in msgs:
        m = re.sub(r"^.*?:\d+:\d+:\s*", "", m.strip())
        m = re.sub(r"\b\d+:\d+\b", "", m)
        m = re.sub(r"\s+", " ", m)
        if m:
            out.add(m)
    return out

def crashed(rc, out):
    return rc not in (0, 1, 2) or "raised " in out or "panicked" in out or "runtime error" in out.lower()

def gnat_env():
    env = dict(os.environ)
    if shutil.which("gprbuild"):
        return env, True
    tc = next((p for p in (HOME / "AppData" / "Local" / "alire" / "cache" / "toolchains",
                           HOME / ".local" / "share" / "alire" / "toolchains",
                           HOME / ".cache" / "alire" / "toolchains") if p.exists()), HOME / "_yok_")
    extra = []
    if tc.exists():
        for d in sorted(tc.iterdir()):
            if (d / "bin").exists():
                extra.append(str(d / "bin"))
    if extra and (HOME / ".alire" / "bin").exists():
        extra.append(str(HOME / ".alire" / "bin"))
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env, bool(extra)

def c_static(src, d):
    f = d / "p.c"
    f.write_text(src, encoding="utf-8")
    rc, out = sh(["gcc", "-std=c11", "-Wall", "-Wextra", "-Wconversion", "-Wshadow", "-fanalyzer", "-O2",
                  "-c", str(f), "-o", str(d / "p.o")])
    msgs = [l for l in out.splitlines() if "warning:" in l or "error:" in l]
    return rc == 0, msgs

def c_run(src, d):
    f = d / "r.c"
    f.write_text(src, encoding="utf-8")
    exe = d / "r.exe"
    rc, out = sh([sys.executable, "-m", "ziglang", "cc", "-std=c11", "-O0", "-fsanitize=undefined",
                  "-fsanitize-trap=undefined", str(f), "-o", str(exe)])
    if rc != 0:
        return None, out[-300:]
    rc, out = sh([str(exe)], timeout=30)
    return crashed(rc, out), f"rc={rc} {out[-200:]}"

def misra_static(src, d):
    CPPCHECK = _cppcheck()
    if not CPPCHECK:
        return None, ["cppcheck missing"]
    f = d / "m.c"
    f.write_text(src, encoding="utf-8")
    addon = HERE / "tools" / "cppcheck-addons" / "misra.py"
    if not addon.exists():
        return None, ["MISRA addon missing: python tools/fetch_misra_addon.py"]
    rc, out = sh([CPPCHECK, f"--addon={addon}", f"--addon-python={sys.executable}", "--std=c11", "--enable=warning,style,portability", "--quiet",
                  "--template={id}: {message}", str(f)])
    msgs = [l for l in out.splitlines() if l.strip() and not l.startswith("Checking")]
    return True, msgs

def ada_prepare(files, d, spark=False):
    for name, text in files.items():
        if name not in ("spark.adc", "gnat.adc"):
            (d / name).write_text(text, encoding="utf-8")
    cfg = files.get("gnat.adc", "") + (files.get("spark.adc", "") if spark else "")
    if cfg:
        (d / "gnat.adc").write_text(cfg, encoding="utf-8")
    proj = ('project Default is\n   for Main use ("main.adb");\n   for Object_Dir use "obj";\n'
            '   package Compiler is\n      for Default_Switches ("Ada") use ("-gnatwa", "-gnata", "-gnat2022", "-gnatf");\n'
            + ('      for Local_Configuration_Pragmas use "gnat.adc";\n' if cfg else "")
            + '   end Compiler;\n   package Builder is\n      for Executable ("main.adb") use "main";\n   end Builder;\n'
            + ('   package Prove is\n      for Proof_Switches ("Ada") use ("--level=2", "--timeout=30");\n   end Prove;\n')
            + 'end Default;\n')
    (d / "default.gpr").write_text(proj, encoding="utf-8")

def ada_static_run(files, d, run=True):
    env, ok = gnat_env()
    if not ok:
        return None, ["GNAT missing"], None, ""
    ada_prepare(files, d)
    rc, out = sh(["gprbuild", "-q", "-P", str(d / "default.gpr")], cwd=d, env=env)
    msgs = [l for l in out.splitlines() if "warning:" in l or "error:" in l or "info:" in l]
    built = rc == 0
    rt, rinfo = None, ""
    if built and run:
        exe = next((p for p in (d / "obj" / "main.exe", d / "main.exe", d / "obj" / "main", d / "main")
                    if p.exists()), None)
        if exe is not None:
            rc2, out2 = sh([str(exe)], cwd=d, timeout=30, env=env)
            rt, rinfo = (None if rc2 <= -998 else crashed(rc2, out2)), f"rc={rc2} {out2[-200:]}"
    return built, msgs, rt, rinfo

def spark_static(files, d):
    env, ok = gnat_env()
    if not ok or not shutil.which("gnatprove", path=env["PATH"]):
        return None, ["gnatprove missing"]
    ada_prepare(files, d, spark=True)
    rc, out = sh(["gnatprove", "-P", str(d / "default.gpr"), "--level=2", "--report=fail", "-j0",
                  "--warnings=continue", "--checks-as-errors=off", "--counterexamples=off"], cwd=d, env=env, timeout=900)
    msgs = [l for l in out.splitlines() if re.search(r"(medium|high|low|error|warning):", l)]
    return rc == 0 or bool(msgs), msgs

def rust_static(src, d):
    f = d / "p.rs"
    f.write_text(src, encoding="utf-8")
    drv = str(CARGO / "clippy-driver.exe")
    rc, out = sh([drv, "--edition", "2021", "-W", "clippy::pedantic", "-W", "clippy::all", "--crate-type", "bin",
                  str(f), "-o", str(d / "p_static.exe")])
    msgs = [l for l in out.splitlines() if l.startswith("warning") or l.startswith("error")]
    msgs = [m for m in msgs if not m.startswith("warning: ") or "generated" not in m]
    return rc == 0, msgs

def rust_run(src, d):
    f = d / "r.rs"
    f.write_text(src, encoding="utf-8")
    exe = d / "r.exe"
    rc, out = sh([str(CARGO / "rustc.exe"), "--edition", "2021", "-C", "overflow-checks=on", "-C",
                  "debug-assertions=on", "-C", "opt-level=0", str(f), "-o", str(exe)])
    if rc != 0:
        return None, "did not compile"
    rc, out = sh([str(exe)], timeout=30)
    return crashed(rc, out), f"rc={rc} {out[-200:]}"

def classify(static_m, static_f, run_m, run_f, declares):
    ok_m, msgs_m = static_m
    ok_f, msgs_f = static_f
    if ok_f is None:
        return "—", "tool missing"
    if ok_f is False:
        return "?", "fixed program did not compile: " + "; ".join(msgs_f[:2])
    only = sorted(norm(msgs_m) - norm(msgs_f))
    if not ok_m or only:
        ev = "; ".join(only[:2]) if only else "mutant did not compile"
        return ("◆" if declares else "✓"), ev
    if run_m is not None and run_f is not None:
        if run_f[0]:
            return "?", "fixed program crashed: " + run_f[1]
        if run_m[0]:
            return ("R◆" if declares else "R"), run_m[1]
    return "✗", ""

def measure(mid, spec, work, langs=("C", "MISRA C", "Ada", "SPARK", "Rust")):
    res = {}
    dec = spec.get("declares", {})
    no_run = spec.get("no_run", set())
    c = spec["c"]
    dm, df = work / "c_m", work / "c_f"
    dm.mkdir(), df.mkdir()
    if "C" in langs:
        sm, sf = c_static(c["mutant"], dm), c_static(c["fixed"], df)
        rm = c_run(c["mutant"], dm) if "c" not in no_run else None
        rf = c_run(c["fixed"], df) if "c" not in no_run else None
        res["C"] = classify(sm, sf, rm, rf, dec.get("c", False))
    if "MISRA C" in langs:
        mm, mf = misra_static(c["mutant"], dm), misra_static(c["fixed"], df)
        res["MISRA C"] = classify(mm, mf, None, None, dec.get("c", False))
    a = spec["ada"]
    am, af = work / "a_m", work / "a_f"
    am.mkdir(), af.mkdir()
    if "Ada" in langs:
      bm, msm, rtm, rim = ada_static_run(a["mutant"], am, run="ada" not in no_run)
      bf, msf, rtf, rif = ada_static_run(a["fixed"], af, run="ada" not in no_run)
      res["Ada"] = classify((bm, msm), (bf, msf), (rtm, rim) if rtm is not None else None,
                            (rtf, rif) if rtf is not None else None, dec.get("ada", False))
    pm, pf = work / "s_m", work / "s_f"
    pm.mkdir(), pf.mkdir()
    if "SPARK" in langs:
        res["SPARK"] = classify(spark_static(a["mutant"], pm), spark_static(a["fixed"], pf), None, None,
                                dec.get("ada", False))
    r = spec["rust"]
    rm_, rf_ = work / "r_m", work / "r_f"
    rm_.mkdir(), rf_.mkdir()
    if "Rust" in langs:
        res["Rust"] = classify(rust_static(r["mutant"], rm_), rust_static(r["fixed"], rf_),
                               rust_run(r["mutant"], rm_), rust_run(r["fixed"], rf_), dec.get("rust", False))
    return res

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    args = sys.argv[1:]
    langs = ("C", "MISRA C", "Ada", "SPARK", "Rust")
    for a in list(args):
        if a.startswith("--langs="):
            langs = tuple(x.replace("_", " ") for x in a.split("=", 1)[1].split(","))
            args.remove(a)
    ids = args or sorted(PROGRAMS)
    out_json = HERE / "results.json"
    results = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else {}
    for mid in ids:
        with tempfile.TemporaryDirectory() as t:
            r = measure(mid, PROGRAMS[mid], pathlib.Path(t), langs)
        cells = results.get(mid, {}).get("cells", {})
        cells.update({k: list(v) for k, v in r.items()})
        results[mid] = {"title": PROGRAMS[mid]["title"], "cells": cells,
                        "note": PROGRAMS[mid].get("note", "")}
        print(f"{mid}: " + "  ".join(f"{k} {v[0]}" for k, v in r.items()))
        out_json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

if __name__ == "__main__":
    main()
