import argparse
import json
import math
import os
import pathlib
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SRC = HERE / "src"
sys.path.insert(0, str(ROOT / "compiler"))
sys.path.insert(0, str(ROOT / "compiler" / "tools"))

import unicorn as U
from unicorn import arm_const as UA, riscv_const as UR, sparc_const as US

import minild
import xval_unicorn as XV
from spelfc import checker, codegen, interp, parser, stack

HOME = pathlib.Path(os.path.expanduser("~"))
TC = next((p for p in (HOME / "AppData" / "Local" / "alire" / "cache" / "toolchains",
                       HOME / ".local" / "share" / "alire" / "toolchains") if p.exists()), None)
CARGO = HOME / ".cargo" / "bin"

def _tc(prefix):
    if TC is None:
        return None
    d = next(iter(sorted(TC.glob(prefix + "*"))), None)
    return d

ARM_GNAT, RV_GNAT = _tc("gnat_arm_elf_"), _tc("gnat_riscv64_elf_")
ZIG = [sys.executable, "-m", "ziglang", "cc"]

TGT = {
    "rv32": {"zig": ["-target", "riscv32-freestanding", "-mcpu=generic_rv32+m+a+c"],
             "gcc": (RV_GNAT / "bin" / "riscv64-elf-gcc.exe") if RV_GNAT else None,
             "gccflags": ["-march=rv32imac_zicsr_zifencei", "-mabi=ilp32"],
             "rts": (RV_GNAT / "riscv64-elf" / "lib" / "gnat" / "light-rv32imac") if RV_GNAT else None,
             "rust": ["--target", "riscv32imac-unknown-none-elf"]},
    "r5": {"zig": ["-target", "thumb-freestanding-eabihf", "-mcpu=cortex_r5"],
           "gcc": (ARM_GNAT / "bin" / "arm-eabi-gcc.exe") if ARM_GNAT else None,
           "gccflags": ["-mthumb", "-mcpu=cortex-r5", "-mfpu=vfpv3-d16", "-mfloat-abi=hard"],
           "rts": (ARM_GNAT / "arm-eabi" / "lib" / "gnat" / "light-cortex-m4f") if ARM_GNAT else None,
           "rust": ["--target", "thumbv7r-none-eabihf", "-C", "target-cpu=cortex-r5"]},
    "leon3": {"zig": ["-target", "sparc-freestanding", "-mcpu=leon3"], "gcc": None, "rts": None, "rust": None},
}
CFLAGS = ["-std=c11", "-O2", "-ffreestanding", "-fno-builtin", "-fno-asynchronous-unwind-tables"]
VARIANTS = ["C (GCC)", "C (Clang)", "Ada", "SPARK", "Rust", "Rust+ovf", "Spelf (GCC)", "Spelf (Clang)"]
ADA_UNIT = {"adcs": "wl_adcs", "ccsds": "wl_ccsds", "crc16": "wl_crc16", "px4": "wl_px4", "fbw": "wl_fbw",
            "bdot": "wl_bdot", "lander": "wl_lander"}
ENTRIES = {"px4": ["f_axis_torque", "f_axis_integrate"], "fbw": ["f_fbw_step"], "bdot": ["f_bdot_step"],
           "lander": ["f_lander_step"]}
ALL_WL = "adcs,ccsds,crc16,px4,fbw,bdot,lander"
F32 = lambda v: struct.unpack("f", struct.pack("f", v))[0]

MARK = r"""
__attribute__((noinline)) void spelf_mark(void) { __asm__ volatile("" ::: "memory"); }
#define SPELF_TRAP(n) __attribute__((weak)) void n(void) { for (;;) { } }
SPELF_TRAP(__gnat_last_chance_handler)
SPELF_TRAP(__gnat_rcheck_CE_Overflow_Check)
SPELF_TRAP(__gnat_rcheck_CE_Range_Check)
SPELF_TRAP(__gnat_rcheck_CE_Index_Check)
SPELF_TRAP(__gnat_rcheck_CE_Divide_By_Zero)
SPELF_TRAP(__gnat_rcheck_CE_Discriminant_Check)
SPELF_TRAP(__gnat_rcheck_CE_Invalid_Data)
SPELF_TRAP(__gnat_rcheck_CE_Explicit_Raise)
SPELF_TRAP(__gnat_rcheck_PE_Explicit_Raise)
"""

def unit_q(rng):
    v = [rng.gauss(0, 1) for _ in range(4)]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [max(-1.0, min(1.0, x / n)) for x in v]

def gen_inputs(wl, rng):
    if wl == "adcs":
        rows = []
        for i in range(120):
            q = unit_q(rng)
            if i % 10 == 0:
                r = list(q)
            elif i % 10 == 1:
                r = [-x for x in q]
            else:
                r = unit_q(rng)
            sc = 0.5 if i % 7 == 0 else 0.05
            w = [max(-0.5, min(0.5, rng.uniform(-sc, sc))) for _ in range(3)]
            rows.append([i % 3] + q + r + w)
        return rows
    if wl == "ccsds":
        rows = []
        for i in range(120):
            b = [rng.randrange(256) for _ in range(6)]
            if i % 4 != 0:
                b[0] &= 0x1F
            if i % 9 == 0:
                b[0] = (b[0] & 0xE0) | 0x07
                b[1] = 0xFF
            dlen = b[4] * 256 + b[5] + 1
            rx = rng.choice([rng.randrange(0, 6), 6 + dlen, 6 + dlen - 1, rng.randrange(0, 65536)])
            rows.append(b + [max(0, min(65535, rx))])
        return rows
    if wl == "crc16":
        rows = [[49 + k for k in range(9)] + [0] * 55 + [9]]
        for _ in range(23):
            rows.append([rng.randrange(256) for _ in range(64)] + [rng.randrange(0, 65)])
        return rows
    if wl == "px4":
        f32 = lambda v: struct.unpack("f", struct.pack("f", v))[0]
        rows = []
        for _ in range(200):
            sp, rate = f32(rng.uniform(-35, 35)), f32(rng.uniform(-35, 35))
            dt = f32(rng.choice([0.000125, 0.004, 0.0025, 0.02, rng.uniform(0.000125, 0.02)]))
            rows.append([rng.randrange(3), f32(sp - rate), sp, f32(rng.uniform(-500, 500)),
                         min(max(dt, f32(0.000125)), f32(0.02)), int(rng.random() < 0.2), int(rng.random() < 0.2)])
        return rows
    if wl == "fbw":
        return fbw_inputs(rng)
    if wl == "bdot":
        return bdot_inputs(rng)
    if wl == "lander":
        return lander_inputs(rng)
    raise KeyError(wl)

def _clip(v, lo, hi):
    return F32(max(lo, min(hi, v)))

def fbw_inputs(rng, n=320):
    rows, q, a = [], 0.0, 0.05
    for k in range(n):
        stick = _clip(math.sin(k / 23.0) * (1.0 if k % 90 < 60 else -0.4), -1.0, 1.0)
        q = 0.92 * q + 0.08 * stick * 0.4 + rng.gauss(0, 0.004)
        a = 0.97 * a + 0.03 * (0.05 + 0.9 * q)
        qs = [_clip(q + rng.gauss(0, 0.002), -2.0, 2.0) for _ in range(3)]
        al = [_clip(a + rng.gauss(0, 0.001), -0.6, 0.6) for _ in range(3)]
        if 100 <= k < 140:
            qs[2] = 2.0
        if k >= 200:
            al[1] = 0.6
        if k % 50 == 7:
            al = [0.55, 0.52, 0.58]
        qb = {40: 10000.0, 80: 30000.0, 120: 1000.0, 160: 60000.0}.get(k, 1000.0 + (k * 397.0) % 59000.0)
        rows.append([stick] + qs + al + [F32(qb)])
    return rows

def bdot_inputs(rng, n=240):
    rows = []
    for k in range(n):
        t = k * 0.1
        bx = 45.0 * math.cos(0.15 * t) + rng.gauss(0, 0.05)
        by = 45.0 * math.sin(0.15 * t) * math.cos(0.07 * t) + rng.gauss(0, 0.05)
        bz = 30.0 * math.sin(0.07 * t) + rng.gauss(0, 0.05)
        valid = 0 if k in (60, 61, 150) else 1
        if k == 100:
            bx = 80.0
        rows.append([_clip(by, -80, 80), _clip(-bx, -80, 80), _clip(bz, -80, 80), valid])
    return rows

def lander_inputs(rng, n=360):
    rows, h, v = [], 300.0, -28.0
    for k in range(n):
        vref = max(-20.0, -0.1 * h - 1.0)
        v = v + 0.25 * (vref - v) + rng.gauss(0, 0.05)
        h = max(0.0, h + v * 0.01 * 6)
        st = 3 if k in (90, 91, 92) else (1 if k == 150 else 0)
        alt = [st, _clip(h + rng.gauss(0, 0.3), 0.0, 2500.0)]
        legs = [[0, 0], [0, 0], [0, 0]]
        if k in (15, 16):
            legs = [[0, 1], [0, 1], [0, 1]]
        if h <= 0.0:
            legs = [[0, 1], [3, 0], [0, 1]]
        rows.append([alt, _clip(v, -80.0, 10.0)] + legs)
    return rows

def reference(wl, rows):
    src = (SRC / f"{wl}.spelf").read_text(encoding="utf-8")
    ck = checker.check(parser.parse(src, f"{wl}.spelf"), f"{wl}.spelf")
    it = interp.Interp(ck)
    out = []
    for r in rows:
        if wl == "adcs":
            out.append(f"{it.call('wl_adcs', r):.17g}")
        elif wl == "ccsds":
            out.append(str(it.call("wl_ccsds", r)))
        elif wl == "crc16":
            out.append(str(it.call("wl_crc16", [r[:64], r[64]])))
        elif wl == "px4":
            k, err, sp, acc, dt, a, b = r
            out.append(f"{it.call('axis_torque', [k, err, sp, acc]):.9g}")
            it.call("axis_integrate", [k, err, dt, bool(a), bool(b)])
        elif wl == "fbw":
            out.append(f"{it.call('fbw_step', r):.9g}")
        elif wl == "bdot":
            d = it.call("bdot_step", [r[0], r[1], r[2], bool(r[3])])
            out += [f"{d[f]:.9g}" for f in ("x", "y", "z")]
        else:
            alt, vz, l1, l2, l3 = r
            sus = lambda x: (x[0], bool(x[1]))
            out.append(f"{it.call('lander_step', [tuple(alt), vz, sus(l1), sus(l2), sus(l3)]):.9g}")
    return out, ck

def harness(wl, rows) -> str:
    h = ["#include <stdint.h>", "#include <stdbool.h>", "void spelf_mark(void);",
         "void spelf_print_i64(int64_t v);", "void spelf_print_f64(double v);"]
    if wl == "adcs":
        h += ["double f_wl_adcs(int32_t, double, double, double, double, double, double, double, double, "
              "double, double, double);",
              "static const int32_t K[] = {" + ", ".join(str(r[0]) for r in rows) + "};",
              "static const double X[][11] = {" + ", ".join("{" + ", ".join(repr(float(v)) for v in r[1:]) + "}"
                                                           for r in rows) + "};",
              f"int main(void) {{ spelf_mark(); spelf_mark(); for (int i = 0; i < {len(rows)}; ++i) {{ "
              "const double *x = X[i]; spelf_mark(); double r = f_wl_adcs(K[i], x[0], x[1], x[2], x[3], x[4], x[5], "
              "x[6], x[7], x[8], x[9], x[10]); spelf_mark(); spelf_print_f64(r); } return 0; }"]
    elif wl == "ccsds":
        h += ["int32_t f_wl_ccsds(int32_t, int32_t, int32_t, int32_t, int32_t, int32_t, int32_t);",
              "static const int32_t X[][7] = {" + ", ".join("{" + ", ".join(map(str, r)) + "}" for r in rows) + "};",
              f"int main(void) {{ spelf_mark(); spelf_mark(); for (int i = 0; i < {len(rows)}; ++i) {{ "
              "const int32_t *x = X[i]; spelf_mark(); int32_t r = f_wl_ccsds(x[0], x[1], x[2], x[3], x[4], x[5], x[6]); "
              "spelf_mark(); spelf_print_i64(r); } return 0; }"]
    elif wl == "crc16":
        h += ["int32_t f_wl_crc16(const uint8_t *buf, int32_t n);",
              "static const uint8_t B[][64] = {" + ", ".join("{" + ", ".join(map(str, r[:64])) + "}" for r in rows) + "};",
              "static const int32_t N[] = {" + ", ".join(str(r[64]) for r in rows) + "};",
              f"int main(void) {{ spelf_mark(); spelf_mark(); for (int i = 0; i < {len(rows)}; ++i) {{ "
              "spelf_mark(); int32_t r = f_wl_crc16(B[i], N[i]); spelf_mark(); spelf_print_i64(r); } return 0; }"]
    elif wl == "fbw":
        h += ["float f_fbw_step(float, float, float, float, float, float, float, float);",
              "static const float X[][8] = {" + ", ".join("{" + ", ".join(repr(float(v)) + "f" for v in r) + "}"
                                                         for r in rows) + "};",
              f"int main(void) {{ spelf_mark(); spelf_mark(); for (int i = 0; i < {len(rows)}; ++i) {{ "
              "const float *x = X[i]; spelf_mark(); float r = f_fbw_step(x[0], x[1], x[2], x[3], x[4], x[5], x[6], "
              "x[7]); spelf_mark(); spelf_print_f64((double)r); } return 0; }"]
    elif wl == "bdot":
        h += ["typedef struct { float x; float y; float z; } D;",
              "void f_bdot_step(float, float, float, bool, D *);",
              "static const float X[][3] = {" + ", ".join("{" + ", ".join(repr(float(v)) + "f" for v in r[:3]) + "}"
                                                         for r in rows) + "};",
              "static const uint8_t V[] = {" + ", ".join(str(r[3]) for r in rows) + "};",
              f"int main(void) {{ spelf_mark(); spelf_mark(); for (int i = 0; i < {len(rows)}; ++i) {{ "
              "const float *x = X[i]; D d; spelf_mark(); f_bdot_step(x[0], x[1], x[2], V[i] != 0, &d); spelf_mark(); "
              "spelf_print_f64((double)d.x); spelf_print_f64((double)d.y); spelf_print_f64((double)d.z); } "
              "return 0; }"]
    elif wl == "lander":
        h += ["typedef struct { int32_t s; bool v; } L;", "typedef struct { int32_t s; float v; } H;",
              "float f_lander_step(H, float, L, L, L);",
              "static const H A[] = {" + ", ".join(f"{{{r[0][0]}, {float(r[0][1])!r}f}}" for r in rows) + "};",
              "static const float VZ[] = {" + ", ".join(repr(float(r[1])) + "f" for r in rows) + "};",
              "static const L G[][3] = {" + ", ".join("{" + ", ".join(f"{{{l[0]}, {'true' if l[1] else 'false'}}}"
                                                                     for l in r[2:]) + "}" for r in rows) + "};",
              f"int main(void) {{ spelf_mark(); spelf_mark(); for (int i = 0; i < {len(rows)}; ++i) {{ "
              "spelf_mark(); float t = f_lander_step(A[i], VZ[i], G[i][0], G[i][1], G[i][2]); spelf_mark(); "
              "spelf_print_f64((double)t); } return 0; }"]
    else:
        lit = lambda v: repr(float(v)) + "f"
        h += ["float f_axis_torque(int32_t k, float err, float sp, float acc);",
              "void f_axis_integrate(int32_t k, float err, float dt, bool sp, bool sn);",
              "static const int32_t K[] = {" + ", ".join(str(r[0]) for r in rows) + "};",
              "static const float X[][4] = {" + ", ".join("{" + ", ".join(lit(v) for v in (r[1], r[2], r[3], r[4])) + "}"
                                                         for r in rows) + "};",
              "static const uint8_t S[][2] = {" + ", ".join(f"{{{r[5]}, {r[6]}}}" for r in rows) + "};",
              f"int main(void) {{ spelf_mark(); spelf_mark(); for (int i = 0; i < {len(rows)}; ++i) {{ "
              "const float *x = X[i]; spelf_mark(); float t = f_axis_torque(K[i], x[0], x[1], x[2]); "
              "f_axis_integrate(K[i], x[0], x[3], S[i][0] != 0, S[i][1] != 0); spelf_mark(); "
              "spelf_print_f64((double)t); } return 0; }"]
    return "\n".join(h) + "\n"

def run(cmd, cwd=None):
    cp = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                        errors="replace")
    if cp.returncode != 0:
        raise RuntimeError(" ".join(str(c) for c in cmd[:6]) + " …\n" + (cp.stdout + cp.stderr)[-2500:])
    return cp

def build_variant(wl, variant, target, work: pathlib.Path):
    t = TGT[target]
    work.mkdir(parents=True, exist_ok=True)
    if variant.startswith("Spelf"):
        src = (SRC / f"{wl}.spelf").read_text(encoding="utf-8")
        ck = checker.check(parser.parse(src, f"{wl}.spelf"), f"{wl}.spelf")
        (work / "wl.c").write_text(codegen.generate(ck, freestanding=True), encoding="utf-8")
        csrc = work / "wl.c"
    elif variant.startswith("C "):
        csrc = SRC / f"{wl}.c"
    else:
        csrc = None
    if csrc is not None:
        if "GCC" in variant:
            if t["gcc"] is None:
                return None
            cc = [t["gcc"], *t["gccflags"], *CFLAGS]
        else:
            cc = [*ZIG, *t["zig"], *CFLAGS]
        run([*cc, "-c", csrc, "-o", work / "wl.o"])
        asm = run([*cc, "-S", csrc, "-o", work / "wl.s"]) and (work / "wl.s").read_text(encoding="utf-8", errors="replace")
        return [work / "wl.o"], asm, []
    if variant in ("Ada", "SPARK"):
        if t["gcc"] is None:
            return None
        unit = ADA_UNIT[wl]
        for ext in ("ads", "adb"):
            shutil.copy(SRC / f"{unit}.{ext}", work / f"{unit}.{ext}")
        cc = [t["gcc"], *t["gccflags"], "-O2", "-gnat2022", f"--RTS={t['rts']}", "-fno-asynchronous-unwind-tables"]
        if variant == "SPARK":
            cc.append("-gnatp")
        run([*cc, "-c", f"{unit}.adb"], cwd=work)
        run([*cc, "-S", f"{unit}.adb"], cwd=work)
        return [work / f"{unit}.o"], (work / f"{unit}.s").read_text(encoding="utf-8", errors="replace"), []
    if variant.startswith("Rust"):
        if t["rust"] is None:
            return None
        cmd = [CARGO / "rustc.exe", "--edition", "2021", "--crate-type", "staticlib", "-C", "opt-level=2", "-C",
               "panic=abort", "-C", "codegen-units=1", "-C", "debuginfo=0", *t["rust"],
               "-C", f"overflow-checks={'on' if variant == 'Rust+ovf' else 'off'}",
               f"--emit=link={work / 'libwl.a'},obj={work / 'wl.o'},asm={work / 'wl.s'}", SRC / f"{wl}.rs"]
        run(cmd)
        return [work / "wl.o"], (work / "wl.s").read_text(encoding="utf-8", errors="replace"), [work / "libwl.a"]
    raise KeyError(variant)

def link_image(wl, rows, target, objs, extra, work: pathlib.Path):
    t = TGT[target]
    (work / "h.c").write_text(harness(wl, rows), encoding="utf-8")
    rt = XV.HARNESS.replace("void *memcpy(", "__attribute__((weak)) void *memcpy(")                    .replace("void *memset(", "__attribute__((weak)) void *memset(")
    (work / "rt.c").write_text(rt + MARK, encoding="utf-8")
    if target == "leon3":
        o = []
        for name, src_ in (("h", work / "h.c"), ("rt", work / "rt.c")):
            run([*ZIG, *t["zig"], *CFLAGS, "-c", src_, "-o", work / f"{name}.o"])
            o.append(work / f"{name}.o")
        (work / "srt.c").write_text(XV.SPARC_RT, encoding="utf-8")
        run([*ZIG, *t["zig"], *CFLAGS, "-c", work / "srt.c", "-o", work / "srt.o"])
        return minild.link([p.read_bytes() for p in objs + o + [work / "srt.o"]])
    out = work / "img.elf"
    run([*ZIG, *t["zig"], *CFLAGS, "-Wl,-e,main", "-Wl,--gc-sections",
         work / "h.c", work / "rt.c", *objs, *extra, "-o", out])
    return XV.parse_elf(out.read_bytes())

def emulate(img, target, max_insn=80_000_000):
    tg = XV.TARGETS[target]
    mu = U.Uc(tg["arch"], tg["mode"])
    mu.ctl_set_cpu_model(tg["cpu"])
    page = 0x1000
    for vaddr, blob, memsz in img["segments"]:
        lo = vaddr & ~(page - 1)
        hi = (vaddr + max(memsz, len(blob)) + page - 1) & ~(page - 1)
        try:
            mu.mem_map(lo, hi - lo)
        except U.UcError:
            pass
        mu.mem_write(vaddr, blob)
    mu.mem_map(XV.STACK_TOP - XV.STACK_SIZE, XV.STACK_SIZE)
    mu.mem_map(XV.SENTINEL, page)
    syms = img["symbols"]
    main, mark = syms["main"], syms["spelf_mark"] & ~1
    count, marks = [0], []

    def on_code(uc, addr, size, user):
        if (addr & ~1) == mark:
            marks.append(count[0])
        count[0] += 1

    def on_block(uc, addr, size, user):
        if addr == mark:
            marks.append(count[0])
        count[0] += size // 4

    mu.hook_add(U.UC_HOOK_BLOCK if target == "leon3" else U.UC_HOOK_CODE,
                on_block if target == "leon3" else on_code)
    sp = XV.STACK_TOP - 256
    if target == "rv32":
        mu.reg_write(UR.UC_RISCV_REG_SP, sp)
        mu.reg_write(UR.UC_RISCV_REG_RA, XV.SENTINEL)
        begin = main
    elif target == "r5":
        mu.reg_write(UA.UC_ARM_REG_SP, sp)
        mu.reg_write(UA.UC_ARM_REG_LR, XV.SENTINEL | 1)
        mu.reg_write(UA.UC_ARM_REG_C1_C0_2, mu.reg_read(UA.UC_ARM_REG_C1_C0_2) | (0xF << 20))
        mu.reg_write(UA.UC_ARM_REG_FPEXC, 0x40000000)
        begin = main | 1
    else:
        mu.reg_write(US.UC_SPARC_REG_SP, sp - 96)
        mu.reg_write(US.UC_SPARC_REG_O7, XV.SENTINEL - 8)
        mu.reg_write(US.UC_SPARC_REG_PSR, 0x10C0)
        begin = main
    mu.emu_start(begin, XV.SENTINEL, count=0 if target == "leon3" else max_insn)
    e = img["endian"]
    n = struct.unpack(e + "I", mu.mem_read(syms["spelf_out_n"], 4))[0]
    ks = mu.mem_read(syms["spelf_out_k"], n)
    vs = mu.mem_read(syms["spelf_out_v"], 8 * n)
    out = []
    for i in range(n):
        raw = struct.unpack(e + "Q", vs[8 * i:8 * i + 8])[0]
        if ks[i] == 1:
            out.append(str(struct.unpack("<q", struct.pack("<Q", raw))[0]))
        else:
            out.append(struct.unpack("<d", struct.pack("<Q", raw))[0])
    overhead = marks[1] - marks[0]
    per_call = [marks[i + 1] - marks[i] - overhead for i in range(2, len(marks) - 1, 2)]
    return out, per_call

def text_size(obj: pathlib.Path) -> int:
    o = minild._Obj(obj.read_bytes())
    return sum(s[5] for s in o.secs if (s[2] & 0x6) == 0x6)

CHECK_RE = re.compile(r"^\s*(call|tail|jal|j|bl|blx|b|b\.w|beq\.w|bne\.w|call)\s+\S*(panic|rcheck|last_chance)", re.M)

def check_sites(asm: str) -> int:
    return len(CHECK_RE.findall(asm))

def worst_stack(asm: str, target: str, entries) -> int | None:
    try:
        fns, _ = stack.parse_asm(asm, target)
    except Exception:
        return None
    memo = {}

    def depth(f, seen=()):
        if f not in fns:
            return 0
        if f in seen:
            return 0
        if f in memo:
            return memo[f]
        d = fns[f]["frame"] + max((depth(c, seen + (f,)) for c in fns[f]["calls"]), default=0)
        memo[f] = d
        return d
    vals = [depth(e) for e in entries if e in fns]
    return max(vals) if vals else None

def fmt_out(v):
    return v if isinstance(v, str) else None

def compare(wl, got, ref):
    if len(got) != len(ref):
        return False
    for g, r in zip(got, ref):
        if isinstance(g, float):
            g = f"{g:.17g}" if wl == "adcs" else f"{struct.unpack('f', struct.pack('f', g))[0]:.9g}"
        if g != r:
            return False
    return True

def spark_proof(wl) -> dict:
    import importlib.util
    ml_dir = ROOT / "benchmark" / "multilang"
    sys.path.insert(0, str(ml_dir))
    if "multilang_run" not in sys.modules:
        spec = importlib.util.spec_from_file_location("multilang_run", ml_dir / "run.py")
        sys.modules["multilang_run"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules["multilang_run"])
    ML = sys.modules["multilang_run"]
    unit = ADA_UNIT[wl]
    files = {f"{unit}.ads": (SRC / f"{unit}.ads").read_text(encoding="utf-8"),
             f"{unit}.adb": (SRC / f"{unit}.adb").read_text(encoding="utf-8"),
             "main.adb": f"with {unit.title()};\nprocedure Main with SPARK_Mode => Off is\nbegin\n   null;\nend Main;\n"}
    with tempfile.TemporaryDirectory() as d:
        ok, msgs = ML.spark_static(files, pathlib.Path(d))
        summary = ""
        rep = pathlib.Path(d) / "obj" / "gnatprove" / "gnatprove.out"
        if rep.exists():
            m = re.search(r"^Total\s+(\d+)\s+.*$", rep.read_text(encoding="utf-8", errors="replace"), re.M)
            summary = m.group(0) if m else ""
    total = int(re.search(r"Total\s+(\d+)", summary).group(1)) if summary else None
    return {"clean": bool(ok) and not msgs, "messages": msgs[:5], "checks": total}

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--wl", default=ALL_WL)
    ap.add_argument("--targets", default="rv32,r5,leon3")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--no-spark-proof", action="store_true")
    a = ap.parse_args()
    variants = a.variants.split(",")
    out_json = HERE / "results.json"
    results = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else {}
    for wl in a.wl.split(","):
        rows = gen_inputs(wl, random.Random(2026))
        ref, ck = reference(wl, rows)
        res = results.setdefault(wl, {"cells": {}})
        res["calls"] = len(rows)
        res["spelf_obligations"] = sum(ck.stats.values())
        if not a.no_spark_proof and "SPARK" in variants:
            res["spark"] = spark_proof(wl)
            print(f"[{wl}] GNATprove: {'clean' if res['spark']['clean'] else 'DIAGNOSTICS: ' + '; '.join(res['spark']['messages'][:2])}"
                  f" (checks: {res['spark']['checks']})")
        entries = ENTRIES.get(wl, [f"f_wl_{wl}"])
        for target in a.targets.split(","):
            for v in variants:
                key = f"{v} | {target}"
                with tempfile.TemporaryDirectory() as d:
                    work = pathlib.Path(d)
                    try:
                        b = build_variant(wl, v, target, work / "b")
                        if b is None:
                            res["cells"][key] = {"status": "—"}
                            continue
                        objs, asm, extra = b
                        img = link_image(wl, rows, target, objs, extra, work / "b")
                        got, per_call = emulate(img, target)
                    except Exception as ex:
                        res["cells"][key] = {"status": "error", "error": str(ex)[-600:]}
                        print(f"[{wl}] {key}: ERROR {str(ex)[-300:]}")
                        continue
                    cell = {"status": "ok", "equal": compare(wl, got, ref),
                            "insn_mean": round(sum(per_call) / len(per_call), 1), "insn_max": max(per_call),
                            "text": sum(text_size(o) for o in objs), "stack": worst_stack(asm, target, entries),
                            "checks": check_sites(asm), "per_call": per_call}
                    res["cells"][key] = cell
                    print(f"[{wl}] {key:22s} equal={cell['equal']}  instr/call mean {cell['insn_mean']:8.1f} "
                          f"worst {cell['insn_max']:6d}  .text {cell['text']:5d} B  stack {cell['stack']}  "
                          f"checks {cell['checks']}")
        out_json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

if __name__ == "__main__":
    main()
