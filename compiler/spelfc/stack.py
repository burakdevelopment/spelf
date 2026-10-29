import re
import subprocess
import sys
import tempfile
import pathlib

TARGETS = {
    "leon3": ["-target", "sparc-freestanding", "-mcpu=leon3"],
    "rv32": ["-target", "riscv32-freestanding", "-mcpu=generic_rv32"],
    "r5": ["-target", "thumb-freestanding", "-mcpu=cortex_r5"],
}
FLAGS = ["-std=c11", "-O2", "-g0", "-ffreestanding", "-fomit-frame-pointer", "-fno-asynchronous-unwind-tables"]
SUBSTRATE = pathlib.Path(__file__).resolve().parent.parent / "runtime" / "spelf_baremetal.c"

def _count_regs(lst: str) -> int:
    n = 0
    for part in lst.split(","):
        part = part.strip()
        m = re.fullmatch(r"([rds])(\d+)\s*-\s*[rds](\d+)", part)
        if m:
            n += int(m.group(3)) - int(m.group(2)) + 1
        elif part:
            n += 1
    return n

def compile_asm(csrc: str, target: str, extra: list | None = None) -> str:
    with tempfile.TemporaryDirectory() as d:
        c = pathlib.Path(d) / "u.c"
        s = pathlib.Path(d) / "u.s"
        c.write_text(csrc, encoding="utf-8")
        cmd = [sys.executable, "-m", "ziglang", "cc", *TARGETS[target], *FLAGS, *(extra or []), "-S", str(c), "-o", str(s)]
        cp = subprocess.run(cmd, capture_output=True, text=True)
        if cp.returncode != 0:
            raise RuntimeError("target compilation failed:\n" + cp.stderr)
        return s.read_text(encoding="utf-8", errors="replace")

def parse_asm(asm: str, target: str) -> tuple[dict, set]:
    fns, globl, cur = {}, set(), None
    funcs = set(re.findall(r"^\s*\.type\s+([\w.$]+),\s*[@%]function", asm, re.M))
    for raw in asm.splitlines():
        line = raw.split({"leon3": "!", "rv32": "#", "r5": "@"}[target])[0].strip()
        m = re.match(r"\.globl\s+([\w.$]+)", line)
        if m:
            globl.add(m.group(1))
            continue
        m = re.match(r"([\w.$]+):$", line)
        if m and m.group(1) in funcs:
            cur = {"frame": 0, "calls": set(), "unknown_frame": False}
            fns[m.group(1)] = cur
            continue
        if cur is None or not line or line.startswith("."):
            if line.startswith(".size") or line.startswith(".Lfunc_end"):
                cur = None if line.startswith(".size") else cur
            continue
        if target == "leon3":
            m = re.match(r"save\s+%sp,\s*(-?\d+),\s*%sp", line)
            if m:
                cur["frame"] += -int(m.group(1))
            elif re.match(r"save\s+%sp,\s*%\w+,\s*%sp", line) or re.match(r"(add|sub)\s+%sp,", line):
                cur["unknown_frame"] = True
            m = re.match(r"call\s+([\w.$]+)", line)
            if m:
                cur["calls"].add(m.group(1))
        elif target == "rv32":
            m = re.match(r"addi\s+sp,\s*sp,\s*(-?\d+)", line)
            if m and int(m.group(1)) < 0:
                cur["frame"] += -int(m.group(1))
            elif re.match(r"(sub|add)\s+sp,\s*sp,\s*[a-z]", line):
                cur["unknown_frame"] = True
            m = re.match(r"(call|tail)\s+([\w.$]+)", line) or re.match(r"jal\s+(ra,\s*)?([\w.$]+)$", line)
            if m and not m.group(2).startswith(".L"):
                cur["calls"].add(m.group(2))
        else:
            m = re.match(r"(push|stmdb\s+sp!,)(\.w)?\s*\{([^}]*)\}", line)
            if m:
                cur["frame"] += 4 * _count_regs(m.group(3))
            m = re.match(r"vpush(\.\d+)?\s*\{([^}]*)\}", line)
            if m:
                regs = m.group(2)
                cur["frame"] += (8 if "d" in regs else 4) * _count_regs(regs)
            m = re.match(r"sub(\.w|s)?\s+sp,\s*(sp,\s*)?#(\d+)", line)
            if m:
                cur["frame"] += int(m.group(3))
            elif re.match(r"sub(\.w|s)?\s+sp,\s*(sp,\s*)?r\d+", line):
                cur["unknown_frame"] = True
            m = re.match(r"blx?\s+([\w.$]+)", line)
            if m and not m.group(1).startswith(".L"):
                cur["calls"].add(m.group(1))
            m = re.match(r"b(\.w)?\s+([\w.$]+)$", line)
            if m and m.group(2) in funcs:
                cur["calls"].add(m.group(2))
    return fns, globl

def c_to_spelf(cname: str) -> str:
    if cname.startswith("f_"):
        return cname[2:]
    if cname.startswith("task_"):
        return "task:" + cname[5:]
    if cname.startswith("po_"):
        obj, op = cname[3:].split("_", 1)
        return f"{obj}.{op.replace('__body', '')}"
    return cname

def analyze(ck, csrc: str, target: str, rt_frame: int | None = None, substrate: bool = True) -> dict:
    asm = compile_asm(csrc, target)
    fns, globl = parse_asm(asm, target)
    sub_fns = {}
    if substrate:
        sub_fns, _ = parse_asm(compile_asm(SUBSTRATE.read_text(encoding="utf-8"), target, ["-fno-builtin"]), target)
        for k, v in sub_fns.items():
            fns.setdefault(k, v)
    bounds = {}
    for cname in fns:
        sf = ck.fns.get(c_to_spelf(cname))
        if sf is not None and sf.recursive is not None:
            bounds[cname] = sf.recursive + 1
    external, unknown_frames, assumed = set(), set(), set()
    memo = {}

    def depth(f, stack):
        if f in memo:
            return memo[f]
        if f not in fns:
            sf = ck.fns.get(f)
            declared = next((e.value for e in (sf.effects if sf is not None else []) if e.kind == "stack"), None)
            if declared is not None:
                assumed.add(f"{f} ≤ {declared} B (extern contract)")
                return (declared, [f])
            external.add(f)
            return (rt_frame, [f]) if rt_frame is not None else (None, [f])
        info = fns[f]
        if info["unknown_frame"]:
            unknown_frames.add(f)
        best, path = 0, []
        for c in sorted(info["calls"]):
            if c == f or c in stack:
                if c == f and f in bounds:
                    continue
                return (None, [f, c, "(unbounded loop)"])
            d, p = depth(c, stack | {f})
            if d is None:
                memo[f] = (None, [f] + p)
                return memo[f]
            if d > best or not path:
                best, path = d, p
        mult = bounds.get(f, 1)
        r = (info["frame"] * mult + best, [f] + path)
        memo[f] = r
        return r

    entries = {}
    for name in sorted(fns):
        if (name in globl or name.startswith("task_") or name.startswith("isr_")) and name not in sub_fns:
            d, p = depth(name, frozenset())
            entries[name] = {"depth": d, "path": p, "frame": fns[name]["frame"]}
    tasks = {}
    for tname, t in ck.tasks.items():
        e = entries.get(f"{'isr' if t.get('kind') == 'isr' else 'task'}_{tname}")
        budget = t.get("stack")
        tasks[tname] = {"depth": e["depth"] if e else None, "path": e["path"] if e else [],
                        "budget": budget,
                        "ok": (e is not None and e["depth"] is not None and (budget is None or e["depth"] <= budget))}
    return {"target": target, "flags": FLAGS, "substrate": sorted(sub_fns), "functions": {k: {"frame": v["frame"], "calls": sorted(v["calls"])} for k, v in fns.items()},
            "entries": entries, "tasks": tasks, "external": sorted(external), "unknown_frames": sorted(unknown_frames),
            "rt_frame_assumption": rt_frame, "extern_contracts": sorted(assumed)}

def render(res: dict) -> str:
    lines = [f"STACK ANALYSIS (R-201), target {res['target']}, {' '.join(res['flags'][1:])}, from the real assembly"]
    if res.get("substrate"):
        lines.append(f"  substrate included in the analysis: {', '.join(res['substrate'])} (runtime/spelf_baremetal.c)")
    for name, e in res["entries"].items():
        d = "UNKNOWN" if e["depth"] is None else f"{e['depth']} B"
        lines.append(f"  {name:<28} worst {d:>10}   path: {' → '.join(e['path'])}")
    for tname, t in res["tasks"].items():
        b = f"budget {t['budget']} B" if t["budget"] is not None else "no budget declared"
        mark = "✓" if t["ok"] and t["budget"] is not None else ("✗" if not t["ok"] else "·")
        lines.append(f"  task {tname:<22} {mark} {t['depth']} B / {b}")
    if res["external"]:
        a = res["rt_frame_assumption"]
        lines.append(f"  external calls (frame unknown): {', '.join(res['external'])}"
                     + (f", ASSUMPTION: each ≤ {a} B" if a is not None else ", no bound assumed"))
    if res.get("extern_contracts"):
        lines.append(f"  assumed (extern stack contracts): {', '.join(res['extern_contracts'])}")
    if res["unknown_frames"]:
        lines.append(f"  frames that could not be resolved: {', '.join(res['unknown_frames'])}")
    return "\n".join(lines)
