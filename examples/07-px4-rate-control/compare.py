import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
COMP = HERE.parent.parent / "compiler"
sys.path.insert(0, str(COMP))
sys.path.insert(0, str(COMP / "tools"))

from spelfc import checker, codegen, parser, stack
from measure_width import TARGETS, text_size

HARNESS_T = r"""
#include <string.h>
typedef struct {
    REAL gain_p[3], gain_i[3], gain_d[3], gain_ff[3], lim_int[3];
    REAL rate_int[3];
    bool sat_pos[3], sat_neg[3];
} px4_rate_control_t;
void px4_rate_control_update(px4_rate_control_t *rc, const REAL rate[3], const REAL rate_sp[3],
                             const REAL accel[3], REAL dt, bool landed, REAL torque[3]);
static uint64_t st = 0x9E3779B97F4A7C15u;
static double urand(void) { st = st * 6364136223846793005u + 1442695040888963407u;
                            return (double)(st >> 11) * (1.0 / 9007199254740992.0); }
static double span(double lo, double hi) { return lo + (hi - lo) * urand(); }
int main(void) {
    px4_rate_control_t rc; memset(&rc, 0, sizeof rc);
    for (int i = 0; i < 3; i++) { rc.gain_p[i] = (REAL)0.15; rc.gain_i[i] = (REAL)0.2; rc.gain_d[i] = (REAL)0.003;
                                  rc.gain_ff[i] = (REAL)0.0; rc.lim_int[i] = (REAL)0.3; }
    long diff = 0, steps = 20000; double maxabs = 0.0;
    for (long n = 0; n < steps; n++) {
        ARR rate, sp, acc; T_A3b sp_pos, sp_neg;
        REAL r[3], s[3], a[3], tq[3];
        for (int i = 0; i < 3; i++) {
            r[i] = (REAL)(span(-35.0, 35.0) * (urand() < 0.9 ? 0.1 : 1.0));
            s[i] = (REAL)(span(-35.0, 35.0) * (urand() < 0.9 ? 0.1 : 1.0));
            a[i] = (REAL)span(-500.0, 500.0);
            rate.a[i] = r[i]; sp.a[i] = s[i]; acc.a[i] = a[i];
            rc.sat_pos[i] = sp_pos.a[i] = urand() < 0.1;
            rc.sat_neg[i] = sp_neg.a[i] = urand() < 0.1;
        }
        REAL dt = (REAL)span(0.000125, 0.0199);
        bool landed = urand() < 0.05;
        ARR t; f_update(&rate, &sp, &acc, dt, landed, &sp_pos, &sp_neg, &t);
        px4_rate_control_update(&rc, r, s, a, dt, landed, tq);
        for (int i = 0; i < 3; i++) {
            if (memcmp(&t.a[i], &tq[i], sizeof(REAL)) != 0) { diff++; }
            if (tq[i] > maxabs) maxabs = tq[i];
            if (-tq[i] > maxabs) maxabs = -tq[i];
        }
    }
    printf("%ld %ld %.6f\n", steps * 3, diff, maxabs);
    return diff != 0;
}
"""

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    for spelf, refc, real, arr, title in (("rate_control.spelf", "px4_style.c", "double", "T_A3f64", "binary64"),
                                          ("rate_control_f32.spelf", "px4_style_f32.c", "float", "T_A3f32",
                                           "binary32, the precision PX4 uses")):
        print(f"\n══════ {title} ══════")
        run(spelf, refc, real, arr)

def run(spelf, refc, real, arr):
    HARNESS = HARNESS_T.replace("REAL", real).replace("ARR", arr)
    src = (HERE / spelf).read_text(encoding="utf-8")
    ck = checker.check(parser.parse(src, "rate_control.spelf"), "rate_control.spelf")
    hosted = codegen.generate(ck)
    flight = codegen.generate(ck, freestanding=True)
    ref = (HERE / refc).read_text(encoding="utf-8")
    print(f"Spelf check: {sum(ck.stats.values())} obligations proved (IEEE-754 model)")

    cc = shutil.which("gcc")
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "spelf.c").write_text(hosted + HARNESS, encoding="utf-8")
        (d / "ref.c").write_text(ref, encoding="utf-8")
        exe = d / "cmp.exe"
        cp = subprocess.run([cc, "-std=c11", "-O2", "-ffp-contract=off", str(d / "spelf.c"), str(d / "ref.c"),
                             "-o", str(exe)], capture_output=True, text=True)
        if cp.returncode != 0:
            print(cp.stderr[-2000:])
            sys.exit(1)
        out = subprocess.run([str(exe)], capture_output=True, text=True).stdout.split()
        n, diff, mx = int(out[0]), int(out[1]), float(out[2])
        print(f"numerical equivalence: {n} torque outputs, bitwise different: {diff} (largest |torque| {mx:.3f})")

        print("\ncode size (.text, -O2, bytes):")
        print(f"  {'target':<20}{'PX4-style C':>14}{'Spelf':>10}{'diff':>9}")
        rows = []
        targets = TARGETS + [("ARM Cortex-R5F (VFPv3)", ["-target", "thumb-freestanding-eabihf", "-mcpu=cortex_r5",
                                                         "-mfpu=vfpv3-d16", "-mfloat-abi=hard"])]
        for name, flags in targets:
            sizes = []
            for tag, code in (("ref", ref), ("spelf", flight)):
                c = d / f"{tag}.c"
                o = d / f"{tag}.o"
                c.write_text(code, encoding="utf-8")
                cp = subprocess.run([sys.executable, "-m", "ziglang", "cc", *flags, "-std=c11", "-O2",
                                     "-ffreestanding", "-fomit-frame-pointer", "-ffp-contract=off",
                                     "-c", str(c), "-o", str(o)], capture_output=True, text=True)
                if cp.returncode != 0:
                    print(cp.stderr[-800:])
                    sys.exit(1)
                sizes.append(text_size(o))
            rows.append((name, *sizes))
            print(f"  {name:<20}{sizes[0]:>14}{sizes[1]:>10}{(sizes[1] - sizes[0]) / sizes[0] * 100:>+8.0f}%")

        print("\nworst-case stack (from target assembly, bytes; compiler library calls counted without frame):")
        for tg in ("leon3", "rv32", "r5"):
            res = []
            for code, root in ((ref, "px4_rate_control_update"), (flight, "f_update")):
                fns, _ = stack.parse_asm(stack.compile_asm(code, tg), tg)
                memo, lib = {}, set()

                def dep(f):
                    if f in memo:
                        return memo[f]
                    if f not in fns:
                        lib.add(f)
                        return 0
                    memo[f] = fns[f]["frame"] + max((dep(c) for c in fns[f]["calls"]), default=0)
                    return memo[f]
                res.append((dep(root), sorted(lib)))
            libs = sorted(set(res[0][1]) | set(res[1][1]))
            print(f"  {tg:<6} PX4-style C {res[0][0]:>4} B   Spelf {res[1][0]:>4} B   "
                  f"library: {', '.join(libs) or 'none'}")

if __name__ == "__main__":
    main()
