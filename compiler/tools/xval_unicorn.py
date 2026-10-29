import argparse
import pathlib
import random
import struct
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import unicorn as U
from unicorn import arm_const as UA, riscv_const as UR, sparc_const as US

from spelfc import checker, codegen, interp, parser

HARNESS = r"""
#include <stdint.h>
#include <stddef.h>
#define SPELF_OUT_MAX 4096
volatile uint32_t spelf_out_n;
volatile uint8_t spelf_out_k[SPELF_OUT_MAX];
volatile uint64_t spelf_out_v[SPELF_OUT_MAX];
static void spelf_out(uint8_t k, uint64_t v) {
    uint32_t n = spelf_out_n;
    if (n < SPELF_OUT_MAX) { spelf_out_k[n] = k; spelf_out_v[n] = v; spelf_out_n = n + 1u; }
}
void spelf_print_i64(int64_t v) { spelf_out(1u, (uint64_t)v); }
void spelf_print_f64(double v) { union { double d; uint64_t u; } c; c.d = v; spelf_out(2u, c.u); }
void spelf_print_bool(int b) { spelf_out(3u, b ? 1u : 0u); }
void *memcpy(void *restrict d, const void *restrict s, size_t n) {
    uint8_t *dd = (uint8_t *)d; const uint8_t *ss = (const uint8_t *)s;
    for (size_t i = 0; i < n; i++) { dd[i] = ss[i]; }
    return d;
}
void *memset(void *d, int c, size_t n) {
    uint8_t *dd = (uint8_t *)d;
    for (size_t i = 0; i < n; i++) { dd[i] = (uint8_t)c; }
    return d;
}
"""

TARGETS = {
    "leon3": {"zig": ["-target", "sparc-freestanding", "-mcpu=leon3"], "arch": U.UC_ARCH_SPARC,
              "mode": U.UC_MODE_SPARC32 | U.UC_MODE_BIG_ENDIAN, "cpu": US.UC_CPU_SPARC32_LEON3, "end": ">"},
    "rv32": {"zig": ["-target", "riscv32-freestanding", "-mcpu=generic_rv32+m+a+c"], "arch": U.UC_ARCH_RISCV,
             "mode": U.UC_MODE_RISCV32, "cpu": UR.UC_CPU_RISCV32_ANY, "end": "<"},
    "r5": {"zig": ["-target", "thumb-freestanding-eabi", "-mcpu=cortex_r5"], "arch": U.UC_ARCH_ARM,
           "mode": U.UC_MODE_THUMB, "cpu": UA.UC_CPU_ARM_CORTEX_R5F, "end": "<"},
}
FLAGS = ["-std=c11", "-O2", "-g0", "-ffreestanding", "-fno-builtin", "-Wl,-e,main", "-Wl,--gc-sections"]
CFLAGS_OBJ = ["-std=c11", "-O2", "-g0", "-ffreestanding", "-fno-builtin", "-fno-pic", "-fno-asynchronous-unwind-tables"]

SPARC_RT = r"""
#include <stdint.h>
typedef union { uint64_t u; struct { uint32_t hi, lo; } w; } spelf_dw;
uint64_t __lshrdi3(uint64_t a, int b) {
    spelf_dw x, r; x.u = a;
    if (b == 0) { return a; }
    if (b >= 32) { r.w.hi = 0u; r.w.lo = x.w.hi >> (b - 32); }
    else { r.w.hi = x.w.hi >> b; r.w.lo = (x.w.lo >> b) | (x.w.hi << (32 - b)); }
    return r.u;
}
uint64_t __ashldi3(uint64_t a, int b) {
    spelf_dw x, r; x.u = a;
    if (b == 0) { return a; }
    if (b >= 32) { r.w.lo = 0u; r.w.hi = x.w.lo << (b - 32); }
    else { r.w.lo = x.w.lo << b; r.w.hi = (x.w.hi << b) | (x.w.lo >> (32 - b)); }
    return r.u;
}
int64_t __ashrdi3(int64_t a, int b) {
    spelf_dw x, r; x.u = (uint64_t)a;
    int32_t hi = (int32_t)x.w.hi;
    if (b == 0) { return a; }
    if (b >= 32) { r.w.hi = (uint32_t)(hi >> 31); r.w.lo = (uint32_t)(hi >> (b - 32)); }
    else { r.w.hi = (uint32_t)(hi >> b); r.w.lo = (x.w.lo >> b) | (x.w.hi << (32 - b)); }
    return (int64_t)r.u;
}
static uint64_t spelf_udivmod(uint64_t n, uint64_t d, uint64_t *rem) {
    uint64_t q = 0, r = 0;
    for (int i = 63; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1u);
        if (r >= d) { r -= d; q |= (uint64_t)1 << i; }
    }
    if (rem) { *rem = r; }
    return q;
}
uint64_t __udivdi3(uint64_t n, uint64_t d) { return spelf_udivmod(n, d, 0); }
uint64_t __umoddi3(uint64_t n, uint64_t d) { uint64_t r; spelf_udivmod(n, d, &r); return r; }
int64_t __divdi3(int64_t a, int64_t b) {
    uint64_t ua = a < 0 ? 0u - (uint64_t)a : (uint64_t)a, ub = b < 0 ? 0u - (uint64_t)b : (uint64_t)b;
    uint64_t q = spelf_udivmod(ua, ub, 0);
    return ((a < 0) != (b < 0)) ? (int64_t)(0u - q) : (int64_t)q;
}
int64_t __moddi3(int64_t a, int64_t b) {
    uint64_t ua = a < 0 ? 0u - (uint64_t)a : (uint64_t)a, ub = b < 0 ? 0u - (uint64_t)b : (uint64_t)b, r;
    spelf_udivmod(ua, ub, &r);
    return a < 0 ? (int64_t)(0u - r) : (int64_t)r;
}
"""
SENTINEL = 0x00F0_0000
STACK_TOP, STACK_SIZE = 0x0800_0000, 0x0010_0000

def parse_elf(data: bytes):
    if data[:4] != b"\x7fELF" or data[4] != 1:
        raise ValueError("not a 32-bit ELF")
    e = ">" if data[5] == 2 else "<"
    (_, _, _, entry, phoff, shoff, _, _, phentsize, phnum, shentsize, shnum, shstrndx) = \
        struct.unpack(e + "HHIIIIIHHHHHH", data[16:52])
    segs = []
    for i in range(phnum):
        o = phoff + i * phentsize
        ptype, off, vaddr, _, filesz, memsz, _, _ = struct.unpack(e + "IIIIIIII", data[o:o + 32])
        if ptype == 1:
            segs.append((vaddr, data[off:off + filesz], memsz))
    secs = []
    for i in range(shnum):
        o = shoff + i * shentsize
        secs.append(struct.unpack(e + "IIIIIIIIII", data[o:o + 40]))
    syms = {}
    for name, stype, _, _, off, size, link, _, _, entsize in secs:
        if stype == 2:
            strtab = secs[link]
            sbase = strtab[4]
            for j in range(size // 16):
                so = off + j * 16
                nm, val, sz, info, _, _ = struct.unpack(e + "IIIBBH", data[so:so + 16])
                end = data.index(b"\0", sbase + nm)
                syms[data[sbase + nm:end].decode("ascii", "replace")] = val
    return {"endian": e, "entry": entry, "segments": segs, "symbols": syms}

def build(csrc: str, target: str, work: pathlib.Path, extra_src: list | None = None) -> dict:
    if target == "leon3":
        import minild
        (work / "p.c").write_text(csrc, encoding="utf-8")
        (work / "h.c").write_text(HARNESS, encoding="utf-8")
        (work / "rt.c").write_text(SPARC_RT, encoding="utf-8")
        objs = []
        for k, src_ in enumerate([work / "p.c", work / "h.c", work / "rt.c"] + [pathlib.Path(x) for x in (extra_src or [])]):
            obj = work / f"o{k}_leon3.o"
            cmd = [sys.executable, "-m", "ziglang", "cc", *TARGETS[target]["zig"], *CFLAGS_OBJ, "-c", str(src_),
                   "-o", str(obj)]
            cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if cp.returncode != 0:
                raise RuntimeError(f"{target} compilation failed:\n{cp.stderr[-1500:]}")
            objs.append(obj.read_bytes())
        return minild.link(objs)
    (work / "p.c").write_text(csrc, encoding="utf-8")
    (work / "h.c").write_text(HARNESS, encoding="utf-8")
    srcs = [str(work / "p.c"), str(work / "h.c")] + [str(s) for s in (extra_src or [])]
    out = work / f"p_{target}.elf"
    cmd = [sys.executable, "-m", "ziglang", "cc", *TARGETS[target]["zig"], *FLAGS, *srcs, "-o", str(out)]
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        raise RuntimeError(f"{target} compilation failed:\n{cp.stderr[-1500:]}")
    return parse_elf(out.read_bytes())

def emulate(elf: dict, target: str, max_insn: int = 50_000_000):
    t = TARGETS[target]
    mu = U.Uc(t["arch"], t["mode"])
    mu.ctl_set_cpu_model(t["cpu"])
    page = 0x1000
    for vaddr, blob, memsz in elf["segments"]:
        lo = vaddr & ~(page - 1)
        hi = (vaddr + max(memsz, len(blob)) + page - 1) & ~(page - 1)
        try:
            mu.mem_map(lo, hi - lo)
        except U.UcError:
            pass
        mu.mem_write(vaddr, blob)
    mu.mem_map(STACK_TOP - STACK_SIZE, STACK_SIZE)
    mu.mem_map(SENTINEL, page)
    main = elf["symbols"]["main"]
    count = [0]

    def on_code(uc, addr, size, user):
        count[0] += 1

    def on_block(uc, addr, size, user):
        count[0] += size // 4

    mu.hook_add(U.UC_HOOK_BLOCK if target == "leon3" else U.UC_HOOK_CODE,
                on_block if target == "leon3" else on_code)
    sp = STACK_TOP - 256
    if target == "rv32":
        mu.reg_write(UR.UC_RISCV_REG_SP, sp)
        mu.reg_write(UR.UC_RISCV_REG_RA, SENTINEL)
        begin = main
    elif target == "r5":
        mu.reg_write(UA.UC_ARM_REG_SP, sp)
        mu.reg_write(UA.UC_ARM_REG_LR, SENTINEL | 1)
        mu.reg_write(UA.UC_ARM_REG_C1_C0_2, mu.reg_read(UA.UC_ARM_REG_C1_C0_2) | (0xF << 20))
        mu.reg_write(UA.UC_ARM_REG_FPEXC, 0x40000000)
        begin = main | 1
    else:
        mu.reg_write(US.UC_SPARC_REG_SP, sp - 96)
        mu.reg_write(US.UC_SPARC_REG_O7, SENTINEL - 8)
        mu.reg_write(US.UC_SPARC_REG_PSR, 0x10C0)
        begin = main
    mu.emu_start(begin, SENTINEL, count=0 if target == "leon3" else max_insn)
    pc = mu.reg_read({"rv32": UR.UC_RISCV_REG_PC, "r5": UA.UC_ARM_REG_PC, "leon3": US.UC_SPARC_REG_PC}[target])
    if not (SENTINEL <= (pc & ~1) < SENTINEL + 8):
        raise RuntimeError(f"{target}: main did not return (pc = {pc:#x}, {count[0]} instructions)")
    syms, e = elf["symbols"], elf["endian"]
    n = struct.unpack(e + "I", mu.mem_read(syms["spelf_out_n"], 4))[0]
    ks = mu.mem_read(syms["spelf_out_k"], n)
    vs = mu.mem_read(syms["spelf_out_v"], 8 * n)
    out = []
    for i in range(n):
        raw = struct.unpack(e + "Q", vs[8 * i:8 * i + 8])[0]
        if ks[i] == 1:
            out.append(str(struct.unpack("<q", struct.pack("<Q", raw))[0]))
        elif ks[i] == 2:
            out.append(f"{struct.unpack('<d', struct.pack('<Q', raw))[0]:.6f}")
        else:
            out.append("true" if raw else "false")
    return out, count[0]

def compile_spelf(src: str, name: str):
    ck = checker.check(parser.parse(src, name), name)
    return ck, codegen.generate(ck, print_hook=True)

def run_tests(targets):
    from run_tests import header
    import diff_test
    files = [f for d in ("mutants", "features", "ok") for f in sorted((ROOT / "tests" / d).glob("*.spelf"))]
    rows, fails = [], []
    for f in files:
        src = f.read_text(encoding="utf-8")
        expect, out, stub = header(f)
        if expect != "OK" or not out:
            continue
        ck, csrc = compile_spelf(src, f.name)
        ref, _ = interp.run(ck, diff_test.STUBS[stub]() if stub else {})
        counts = {}
        with tempfile.TemporaryDirectory() as d:
            for t in targets:
                try:
                    got, n = emulate(build(csrc, t, pathlib.Path(d), [f.parent / stub] if stub else None), t)
                except Exception as ex:
                    fails.append(f"{f.name} [{t}]: {ex}")
                    counts[t] = None
                    continue
                counts[t] = n
                if got != out or got != ref:
                    fails.append(f"{f.name} [{t}]: emulator {got} ≠ expected {out} / interpreter {ref}")
        rows.append((f.name, len(out), counts))
        print(f"{'SAME ' if all(counts.get(t) for t in targets) else 'ERROR'} {f.name:38s} "
              + "  ".join(f"{t} {counts[t] if counts[t] is not None else '—':>8}" for t in targets))
    return rows, fails

def run_fuzz(n, seed, targets):
    import fuzz_soundness as FZ
    from spelfc.diagnostics import SpelfError
    stats = {"generated": 0, "accepted": 0, "checked": 0, "diff": 0}
    fails = []
    for i in range(n):
        rng = random.Random(seed * 1_000_003 + i)
        g = FZ.Gen(rng)
        src = g.program()
        stats["generated"] += 1
        try:
            ck = checker.Checker(parser.parse(src, "fuzz.spelf"), "fuzz.spelf")
            ck.dead_code_check = False
            ck.check()
        except SpelfError:
            continue
        stats["accepted"] += 1
        calls = []
        for _ in range(6):
            name, params, _ = rng.choice(g.fns)
            calls.append((name, [rng.randint(lo, hi) for _, (lo, hi) in params]))
        it = interp.Interp(ck)
        try:
            expect = [str(it.call(nm, args)) for nm, args in calls]
        except interp.PreconditionFalse:
            continue
        csrc = codegen.generate(ck, print_hook=True)
        body = "\n".join(f"    spelf_print_i64((int64_t)f_{nm}({', '.join(str(a) + ('LL' if abs(a) > 2**31 - 1 else '') for a in args)}));"
                         for nm, args in calls)
        csrc += "\nint main(void) {\n" + body + "\n    return 0;\n}\n"
        stats["checked"] += 1
        with tempfile.TemporaryDirectory() as d:
            for t in targets:
                got, _ = emulate(build(csrc, t, pathlib.Path(d)), t)
                if got != expect:
                    stats["diff"] += 1
                    fails.append(f"fuzz #{i} [{t}]: {got} ≠ {expect}\n{src}")
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{n}: {stats}")
    return stats, fails

def run_px4(n, seed, targets):
    ex = next((ROOT.parent / "examples").glob("*-px4-rate-control"))
    src = (ex / "rate_control_f32.spelf").read_text(encoding="utf-8")
    ck = checker.check(parser.parse(src, "rate_control_f32.spelf"), "rate_control_f32.spelf")
    f32 = lambda v: struct.unpack("f", struct.pack("f", v))[0]
    rng = random.Random(seed)
    steps = []
    for _ in range(n):
        k = rng.randint(0, 2)
        sp = f32(rng.uniform(-35.0, 35.0))
        rate = f32(rng.uniform(-35.0, 35.0))
        err = f32(sp - rate)
        acc = f32(rng.uniform(-500.0, 500.0))
        dt = f32(rng.choice([0.000125, 0.004, 0.0025, 0.02, rng.uniform(0.000125, 0.02)]))
        dt = min(max(dt, f32(0.000125)), f32(0.02))
        steps.append((k, err, sp, acc, dt, rng.random() < 0.2, rng.random() < 0.2))
    it = interp.Interp(ck)
    expect = []
    for k, err, sp, acc, dt, sp_, sn in steps:
        expect.append(f"{it.call('axis_torque', [k, err, sp, acc]):.6f}")
        it.call("axis_integrate", [k, err, dt, sp_, sn])
    csrc = codegen.generate(ck, print_hook=True)
    lit = lambda v: repr(float(v)) + "f"
    body = []
    for k, err, sp, acc, dt, sp_, sn in steps:
        body.append(f"    spelf_print_f64((double)f_axis_torque({k}, {lit(err)}, {lit(sp)}, {lit(acc)}));")
        body.append(f"    f_axis_integrate({k}, {lit(err)}, {lit(dt)}, {int(sp_)}, {int(sn)});")
    csrc += "\nint main(void) {\n" + "\n".join(body) + "\n    return 0;\n}\n"
    res = {}
    with tempfile.TemporaryDirectory() as d:
        for t in targets:
            got, cnt = emulate(build(csrc, t, pathlib.Path(d)), t)
            res[t] = (got == expect, cnt, sum(1 for a, b in zip(got, expect) if a != b))
    return "axis_torque + axis_integrate", res

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--tests", action="store_true")
    ap.add_argument("--fuzz", type=int, default=0)
    ap.add_argument("--px4", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--targets", default="leon3,rv32,r5")
    a = ap.parse_args()
    targets = a.targets.split(",")
    allfails = []
    if a.tests:
        rows, fails = run_tests(targets)
        allfails += fails
        print(f"\nacceptance tests: {len(rows)} programs × {len(targets)} targets: "
              f"{len(rows) * len(targets) - len(fails)} SAME, {len(fails)} different/error")
    if a.fuzz:
        stats, fails = run_fuzz(a.fuzz, a.seed, targets)
        allfails += fails
        print(f"\nrandom programs: {stats} on {len(targets)} targets")
    if a.px4:
        fn, res = run_px4(a.px4, a.seed, targets)
        for t, (ok, cnt, diff) in res.items():
            print(f"PX4 {fn} [{t}]: {a.px4} inputs, {'bit-identical' if ok else f'{diff} DIFFERENT'}; "
                  f"{cnt} instructions (~{cnt // max(a.px4, 1)} per call)")
            if not ok:
                allfails.append(f"px4 [{t}]: {diff} different")
    for f in allfails[:20]:
        print("DIFFERENCES:", f)
    sys.exit(1 if allfails else 0)

if __name__ == "__main__":
    main()
