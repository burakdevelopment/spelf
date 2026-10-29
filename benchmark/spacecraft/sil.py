import argparse
import json
import math
import pathlib
import struct
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as R

import unicorn as U
from unicorn import arm_const as UA, riscv_const as UR, sparc_const as US

DT, STEPS, FLIP, EVERY = 0.1, 6000, 3000, 100
J = (0.05, 0.06, 0.04)

PLANT = r"""
#include <stdint.h>
void spelf_print_f64(double v);
double f_wl_adcs(int32_t, double, double, double, double, double, double, double, double, double, double, double);
static const double J0 = 0.05, J1 = 0.06, J2 = 0.04;
static void deriv(const double q[4], const double w[3], const double t[3], double dq[4], double dw[3]) {
    const double hx = J0 * w[0], hy = J1 * w[1], hz = J2 * w[2];
    const double cx = w[1] * hz - w[2] * hy, cy = w[2] * hx - w[0] * hz, cz = w[0] * hy - w[1] * hx;
    dw[0] = (t[0] - cx) / J0;
    dw[1] = (t[1] - cy) / J1;
    dw[2] = (t[2] - cz) / J2;
    dq[0] = 0.5 * (0.0 - q[1] * w[0] - q[2] * w[1] - q[3] * w[2]);
    dq[1] = 0.5 * (q[0] * w[0] + q[2] * w[2] - q[3] * w[1]);
    dq[2] = 0.5 * (q[0] * w[1] + q[3] * w[0] - q[1] * w[2]);
    dq[3] = 0.5 * (q[0] * w[2] + q[1] * w[1] - q[2] * w[0]);
}
static double clampd(double x, double lo, double hi) { return x < lo ? lo : (x > hi ? hi : x); }
int main(void) {
    double q[4] = { Q0, Q1, Q2, Q3 }, w[3] = { 0.05, -0.04, 0.03 };
    for (int32_t s = 0; s < STEPS; ++s) {
        const double sg = (s >= FLIP) ? -1.0 : 1.0;
        double qe[4], we[3], t[3];
        for (int i = 0; i < 4; ++i) { qe[i] = clampd(sg * q[i], -1.0, 1.0); }
        for (int i = 0; i < 3; ++i) { we[i] = clampd(w[i], -0.5, 0.5); }
        for (int32_t k = 0; k < 3; ++k) {
            t[k] = f_wl_adcs(k, qe[0], qe[1], qe[2], qe[3], 1.0, 0.0, 0.0, 0.0, we[0], we[1], we[2]);
        }
        double k1q[4], k1w[3], k2q[4], k2w[3], k3q[4], k3w[3], k4q[4], k4w[3], qa[4], wa[3];
        deriv(q, w, t, k1q, k1w);
        for (int i = 0; i < 4; ++i) { qa[i] = q[i] + 0.5 * DT * k1q[i]; }
        for (int i = 0; i < 3; ++i) { wa[i] = w[i] + 0.5 * DT * k1w[i]; }
        deriv(qa, wa, t, k2q, k2w);
        for (int i = 0; i < 4; ++i) { qa[i] = q[i] + 0.5 * DT * k2q[i]; }
        for (int i = 0; i < 3; ++i) { wa[i] = w[i] + 0.5 * DT * k2w[i]; }
        deriv(qa, wa, t, k3q, k3w);
        for (int i = 0; i < 4; ++i) { qa[i] = q[i] + DT * k3q[i]; }
        for (int i = 0; i < 3; ++i) { wa[i] = w[i] + DT * k3w[i]; }
        deriv(qa, wa, t, k4q, k4w);
        for (int i = 0; i < 4; ++i) { q[i] = q[i] + DT / 6.0 * (k1q[i] + 2.0 * k2q[i] + 2.0 * k3q[i] + k4q[i]); }
        for (int i = 0; i < 3; ++i) { w[i] = w[i] + DT / 6.0 * (k1w[i] + 2.0 * k2w[i] + 2.0 * k3w[i] + k4w[i]); }
        const double n2 = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3];
        const double sc = (3.0 - n2) * 0.5;
        for (int i = 0; i < 4; ++i) { q[i] = q[i] * sc; }
        if ((s + 1) % EVERY == 0) {
            spelf_print_f64(q[0]); spelf_print_f64(q[1]); spelf_print_f64(q[2]); spelf_print_f64(q[3]);
            spelf_print_f64(w[0]); spelf_print_f64(w[1]); spelf_print_f64(w[2]);
        }
    }
    return 0;
}
"""

MUTANT_C = r"""
#include <stdint.h>
double f_wl_adcs(int32_t k, double qw, double qx, double qy, double qz, double rw, double rx, double ry,
                 double rz, double wx, double wy, double wz)
{
    const double ex = rw * qx - rx * qw - ry * qz + rz * qy;
    const double ey = rw * qy + rx * qz - ry * qw - rz * qx;
    const double ez = rw * qz - rx * qy + ry * qx - rz * qw;
    const double e = (k == 0) ? ex : ((k == 1) ? ey : ez);
    const double w0 = (k == 0) ? wx : ((k == 1) ? wy : wz);
    const double t = 0.0 - 0.02 * e - 0.15 * w0;
    return (t < -0.01) ? -0.01 : ((t > 0.01) ? 0.01 : t);
}
"""

MUTANT_SPELF = """module Spacecraft.AdcsMutant
type Q = Float<1, -1.0..=1.0>;
struct ErrQ { w: Float<1, -4.0..=4.0>, x: Float<1, -4.0..=4.0>, y: Float<1, -4.0..=4.0>, z: Float<1, -4.0..=4.0> }
  invariant w >= 0.0
fn err_quat(qw: Q, qx: Q, qy: Q, qz: Q, rw: Q, rx: Q, ry: Q, rz: Q) -> ErrQ {
  let ew = rw * qw + rx * qx + ry * qy + rz * qz;
  let ex = rw * qx - rx * qw - ry * qz + rz * qy;
  let ey = rw * qy + rx * qz - ry * qw - rz * qx;
  let ez = rw * qz - rx * qy + ry * qx - rz * qw;
  ErrQ { w: ew, x: ex, y: ey, z: ez }
}
"""

FIXED_SPELF = MUTANT_SPELF.replace("module Spacecraft.AdcsMutant", "module Spacecraft.AdcsFixed").replace(
    "  ErrQ { w: ew, x: ex, y: ey, z: ez }",
    "  if ew < 0.0 { ErrQ { w: 0.0 - ew, x: 0.0 - ex, y: 0.0 - ey, z: 0.0 - ez } }\n"
    "  else { ErrQ { w: ew, x: ex, y: ey, z: ez } }")

def q0_initial():
    ax = [1.0, 2.0, 3.0]
    n = math.sqrt(sum(a * a for a in ax))
    h = math.radians(160.0) / 2.0
    return [math.cos(h)] + [math.sin(h) * a / n for a in ax]

def clampd(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)

def reference():
    from spelfc import checker, interp, parser
    src = (R.SRC / "adcs.spelf").read_text(encoding="utf-8")
    it = interp.Interp(checker.check(parser.parse(src, "adcs.spelf"), "adcs.spelf"))
    q, w = q0_initial(), [0.05, -0.04, 0.03]

    def deriv(q, w, t):
        hx, hy, hz = J[0] * w[0], J[1] * w[1], J[2] * w[2]
        cx, cy, cz = w[1] * hz - w[2] * hy, w[2] * hx - w[0] * hz, w[0] * hy - w[1] * hx
        dw = [(t[0] - cx) / J[0], (t[1] - cy) / J[1], (t[2] - cz) / J[2]]
        dq = [0.5 * (0.0 - q[1] * w[0] - q[2] * w[1] - q[3] * w[2]),
              0.5 * (q[0] * w[0] + q[2] * w[2] - q[3] * w[1]),
              0.5 * (q[0] * w[1] + q[3] * w[0] - q[1] * w[2]),
              0.5 * (q[0] * w[2] + q[1] * w[1] - q[2] * w[0])]
        return dq, dw
    out = []
    for s in range(STEPS):
        sg = -1.0 if s >= FLIP else 1.0
        qe = [clampd(sg * x, -1.0, 1.0) for x in q]
        we = [clampd(x, -0.5, 0.5) for x in w]
        t = [it.call("wl_adcs", [k] + qe + [1.0, 0.0, 0.0, 0.0] + we) for k in range(3)]
        k1q, k1w = deriv(q, w, t)
        k2q, k2w = deriv([q[i] + 0.5 * DT * k1q[i] for i in range(4)], [w[i] + 0.5 * DT * k1w[i] for i in range(3)], t)
        k3q, k3w = deriv([q[i] + 0.5 * DT * k2q[i] for i in range(4)], [w[i] + 0.5 * DT * k2w[i] for i in range(3)], t)
        k4q, k4w = deriv([q[i] + DT * k3q[i] for i in range(4)], [w[i] + DT * k3w[i] for i in range(3)], t)
        q = [q[i] + DT / 6.0 * (k1q[i] + 2.0 * k2q[i] + 2.0 * k3q[i] + k4q[i]) for i in range(4)]
        w = [w[i] + DT / 6.0 * (k1w[i] + 2.0 * k2w[i] + 2.0 * k3w[i] + k4w[i]) for i in range(3)]
        n2 = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]
        sc = (3.0 - n2) * 0.5
        q = [x * sc for x in q]
        if (s + 1) % EVERY == 0:
            out += q + w
    return out

def plant_src():
    q = q0_initial()
    src = PLANT
    for i in range(4):
        src = src.replace(f"Q{i}", repr(q[i]))
    return src.replace("STEPS", str(STEPS)).replace("FLIP", str(FLIP)).replace("EVERY", str(EVERY)) \
              .replace("DT", repr(DT))

def emulate_fast(img, target):
    tg = R.XV.TARGETS[target]
    mu = U.Uc(tg["arch"], tg["mode"])
    mu.ctl_set_cpu_model(tg["cpu"])
    for vaddr, blob, memsz in img["segments"]:
        lo, hi = vaddr & ~0xFFF, (vaddr + max(memsz, len(blob)) + 0xFFF) & ~0xFFF
        try:
            mu.mem_map(lo, hi - lo)
        except U.UcError:
            pass
        mu.mem_write(vaddr, blob)
    mu.mem_map(R.XV.STACK_TOP - R.XV.STACK_SIZE, R.XV.STACK_SIZE)
    mu.mem_map(R.XV.SENTINEL, 0x1000)
    main = img["symbols"]["main"]
    sp = R.XV.STACK_TOP - 256
    if target == "rv32":
        mu.reg_write(UR.UC_RISCV_REG_SP, sp)
        mu.reg_write(UR.UC_RISCV_REG_RA, R.XV.SENTINEL)
        begin = main
    elif target == "r5":
        mu.reg_write(UA.UC_ARM_REG_SP, sp)
        mu.reg_write(UA.UC_ARM_REG_LR, R.XV.SENTINEL | 1)
        mu.reg_write(UA.UC_ARM_REG_C1_C0_2, mu.reg_read(UA.UC_ARM_REG_C1_C0_2) | (0xF << 20))
        mu.reg_write(UA.UC_ARM_REG_FPEXC, 0x40000000)
        begin = main | 1
    else:
        mu.reg_write(US.UC_SPARC_REG_SP, sp - 96)
        mu.reg_write(US.UC_SPARC_REG_O7, R.XV.SENTINEL - 8)
        mu.reg_write(US.UC_SPARC_REG_PSR, 0x10C0)
        begin = main
    mu.emu_start(begin, R.XV.SENTINEL)
    syms, e = img["symbols"], img["endian"]
    n = struct.unpack(e + "I", mu.mem_read(syms["spelf_out_n"], 4))[0]
    vs = mu.mem_read(syms["spelf_out_v"], 8 * n)
    return [struct.unpack("<d", struct.pack("<Q", struct.unpack(e + "Q", vs[8 * i:8 * i + 8])[0]))[0] for i in range(n)]

def link_sil(target, objs, extra, work: pathlib.Path):
    t = R.TGT[target]
    (work / "plant.c").write_text(plant_src(), encoding="utf-8")
    rt = R.XV.HARNESS.replace("void *memcpy(", "__attribute__((weak)) void *memcpy(") \
                     .replace("void *memset(", "__attribute__((weak)) void *memset(")
    (work / "rt.c").write_text(rt + R.MARK, encoding="utf-8")
    if target == "leon3":
        o = []
        for name in ("plant", "rt"):
            R.run([*R.ZIG, *t["zig"], *R.CFLAGS, "-c", work / f"{name}.c", "-o", work / f"{name}.o"])
            o.append(work / f"{name}.o")
        (work / "srt.c").write_text(R.XV.SPARC_RT, encoding="utf-8")
        R.run([*R.ZIG, *t["zig"], *R.CFLAGS, "-c", work / "srt.c", "-o", work / "srt.o"])
        return R.minild.link([p.read_bytes() for p in objs + o + [work / "srt.o"]])
    out = work / "sil.elf"
    R.run([*R.ZIG, *t["zig"], *R.CFLAGS, "-Wl,-e,main", "-Wl,--gc-sections", work / "plant.c", work / "rt.c",
           *objs, *extra, "-o", out])
    return R.XV.parse_elf(out.read_bytes())

def angle_deg(q, sign=1.0):
    v = math.sqrt(q[1] ** 2 + q[2] ** 2 + q[3] ** 2)
    return math.degrees(2.0 * math.atan2(v, abs(q[0])))

def summarize(vals):
    rows = []
    for i in range(0, len(vals), 7):
        q, w = vals[i:i + 4], vals[i + 4:i + 7]
        rows.append(((i // 7 + 1) * EVERY * DT, angle_deg(q), math.degrees(math.sqrt(sum(x * x for x in w)))))
    return rows

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default="leon3,rv32,r5")
    ap.add_argument("--variants", default=",".join(R.VARIANTS))
    a = ap.parse_args()
    ref = reference()
    ref_rows = summarize(ref)
    print(f"reference (Python plant + spelfc interpreter): final pointing error {ref_rows[-1][1]:.6f}°, "
          f"|ω| {ref_rows[-1][2]:.6f} deg/s")
    res = {"reference": ref_rows, "cells": {}}
    for target in a.targets.split(","):
        for v in a.variants.split(","):
            with tempfile.TemporaryDirectory() as d:
                work = pathlib.Path(d)
                try:
                    b = R.build_variant("adcs", v, target, work / "b")
                    if b is None:
                        res["cells"][f"{v} | {target}"] = {"status": "—"}
                        continue
                    objs, _, extra = b
                    got = emulate_fast(link_sil(target, objs, extra, work / "b"), target)
                except Exception as ex:
                    res["cells"][f"{v} | {target}"] = {"status": "error", "error": str(ex)[-400:]}
                    print(f"{v} | {target}: ERROR {str(ex)[-200:]}")
                    continue
            same = len(got) == len(ref) and all(struct.pack("<d", x) == struct.pack("<d", y) for x, y in zip(got, ref))
            rows = summarize(got)
            res["cells"][f"{v} | {target}"] = {"status": "ok", "bit_identical": same, "final_err_deg": rows[-1][1],
                                               "final_rate_dps": rows[-1][2]}
            print(f"{v:14s} | {target:5s}  bit-identical to the reference: {same}  final error {rows[-1][1]:.6f}°")
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d)
        (work / "m.c").write_text(MUTANT_C, encoding="utf-8")
        t = R.TGT["rv32"]
        R.run([*R.ZIG, *t["zig"], *R.CFLAGS, "-c", work / "m.c", "-o", work / "m.o"])
        mrows = summarize(emulate_fast(link_sil("rv32", [work / "m.o"], [], work), "rv32"))
    from spelfc import checker, parser
    from spelfc.diagnostics import SpelfError
    try:
        checker.check(parser.parse(MUTANT_SPELF, "adcs_mutant.spelf"), "adcs_mutant.spelf")
        spelf_verdict = "ACCEPTED (not expected)"
    except SpelfError as e:
        spelf_verdict = f"rejected [{e.rule}] {e.message}"
    try:
        checker.check(parser.parse(FIXED_SPELF, "adcs_fixed.spelf"), "adcs_fixed.spelf")
        fixed_verdict = "accepted"
    except SpelfError as e:
        fixed_verdict = f"REJECTED (not expected) [{e.rule}] {e.message}"
    res["mutant"] = {"c_rows": mrows, "spelf": spelf_verdict, "spelf_fixed": fixed_verdict}
    print(f"canonicalising counterpart in Spelf: {fixed_verdict}")
    i_flip = FLIP // EVERY
    print(f"mutant M-17 (C, RV32): error before t = 300 s {mrows[i_flip - 1][1]:.3f}°, largest after "
          f"{max(r[1] for r in mrows[i_flip:]):.3f}°, final {mrows[-1][1]:.3f}°")
    print(f"the same mutant in Spelf: {spelf_verdict}")
    (HERE / "sil_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

if __name__ == "__main__":
    main()
